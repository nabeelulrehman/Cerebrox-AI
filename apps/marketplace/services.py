import base64
import binascii
import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone

import aiohttp
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from google.protobuf.json_format import Parse, ParseError
from jwt import InvalidTokenError
from livekit import api
from livekit.protocol import egress
from livekit.protocol.webhook import WebhookEvent

from apps.classrooms.models import Enrollment
from .models import Resource, Payment, TeachingSession, SessionBooking, SessionAttendance, Review

ALLOWED_EXT = {'.pdf', '.ppt', '.pptx', '.doc', '.docx', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.mp4', '.webm', '.zip'}
# html/svg/js are deliberately excluded: they could run script if ever rendered inline.


class MarketError(Exception):
    pass


logger = logging.getLogger(__name__)


def livekit_configuration_issues():
    """Return safe configuration diagnostics; never include credential values."""
    required = {
        'LIVEKIT_URL': settings.LIVEKIT_URL,
        'LIVEKIT_API_KEY': settings.LIVEKIT_API_KEY,
        'LIVEKIT_API_SECRET': settings.LIVEKIT_API_SECRET,
    }
    issues = []
    for name, value in required.items():
        normalized = value.lower()
        if (
            not value
            or normalized.startswith(('your_', 'your-', 'change-me', '<'))
            or 'your_' in normalized
            or 'your-' in normalized
        ):
            issues.append(f'{name} is missing')
    url = required['LIVEKIT_URL']
    if url and not url.lower().startswith(('wss://', 'ws://')):
        issues.append('LIVEKIT_URL must use wss:// or ws://')
    return issues


def livekit_ready():
    return not livekit_configuration_issues()


def livekit_recording_ready():
    required = (
        settings.LIVEKIT_RECORDING_S3_BUCKET,
        settings.LIVEKIT_RECORDING_S3_REGION,
        settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
        settings.LIVEKIT_RECORDING_S3_SECRET,
    )
    return (
        settings.LIVEKIT_RECORDING_ENABLED
        and settings.LIVEKIT_RECORDING_VERIFIED
        and livekit_ready()
        and all(_configured_secret(value) for value in required)
    )


def _configured_secret(value):
    normalized = (value or '').strip().lower()
    return bool(normalized) and not normalized.startswith(('your_', 'your-', 'change-me', '<'))


def livekit_recording_configuration_issues():
    """Safe diagnostics for recording; never return credential values."""
    issues = []
    if not settings.LIVEKIT_RECORDING_ENABLED:
        issues.append('LIVEKIT_RECORDING_ENABLED is not enabled')
    if not livekit_ready():
        issues.extend(livekit_configuration_issues())
    for name, value in (
        ('LIVEKIT_RECORDING_S3_BUCKET', settings.LIVEKIT_RECORDING_S3_BUCKET),
        ('LIVEKIT_RECORDING_S3_REGION', settings.LIVEKIT_RECORDING_S3_REGION),
        ('LIVEKIT_RECORDING_S3_ACCESS_KEY', settings.LIVEKIT_RECORDING_S3_ACCESS_KEY),
        ('LIVEKIT_RECORDING_S3_SECRET', settings.LIVEKIT_RECORDING_S3_SECRET),
    ):
        if not _configured_secret(value):
            issues.append(f'{name} is missing')
    if not settings.LIVEKIT_RECORDING_VERIFIED:
        issues.append('A real Egress recording and private-storage retrieval test has not been verified')
    return issues


def livekit_room_name(session):
    return f'cerebrox-session-{session.pk}'


def livekit_api_url():
    return settings.LIVEKIT_URL.replace('wss://', 'https://', 1).replace('ws://', 'http://', 1)


def livekit_participant_identity(session, user):
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f'{session.pk}:{user.pk}'.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f'p-{digest[:40]}'


def issue_livekit_token(session, user, *, is_teacher):
    if not livekit_ready():
        raise MarketError('LiveKit configuration issue: ' + '; '.join(livekit_configuration_issues()))

    from livekit import api

    token = api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
    token.with_identity(livekit_participant_identity(session, user))
    token.with_name(user.get_full_name() or user.get_username())
    token.with_ttl(timedelta(minutes=10))
    token.with_grants(api.VideoGrants(
        room_join=True,
        room=livekit_room_name(session),
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        room_admin=is_teacher,
    ))
    return token.to_jwt()


def parse_livekit_webhook(body, authorization):
    if not livekit_ready() or not authorization:
        raise MarketError('LiveKit webhook authentication is not configured.')
    try:
        claims = api.TokenVerifier(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET).verify(authorization)
    except (InvalidTokenError, ValueError) as exc:
        raise MarketError('Invalid LiveKit webhook authorization.') from exc
    if not claims.sha256:
        raise MarketError('LiveKit webhook signature does not include a body digest.')
    try:
        expected_digest = base64.b64decode(claims.sha256, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MarketError('LiveKit webhook body digest is invalid.') from exc
    actual_digest = hashlib.sha256(body).digest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise MarketError('LiveKit webhook body digest does not match.')
    try:
        return Parse(body.decode('utf-8'), WebhookEvent(), ignore_unknown_fields=True)
    except (UnicodeDecodeError, ParseError) as exc:
        raise MarketError('LiveKit webhook payload is invalid.') from exc


def process_livekit_webhook(event):
    if event.event in ('participant_joined', 'participant_left'):
        prefix = 'cerebrox-session-'
        room_name = event.room.name
        if not room_name.startswith(prefix) or not room_name[len(prefix):].isdigit():
            return
        session = TeachingSession.objects.filter(pk=int(room_name[len(prefix):])).first()
        if session is None or livekit_room_name(session) != room_name or not event.participant.identity:
            return
        if not event.created_at:
            return
        event_at = datetime.fromtimestamp(event.created_at, tz=datetime_timezone.utc)
        for booking in session.bookings.select_related('student').all().iterator():
            student = booking.student
            if livekit_participant_identity(session, student) != event.participant.identity:
                continue
            if event.event == 'participant_joined':
                if booking.status != SessionBooking.Status.CONFIRMED or session.status != TeachingSession.Status.ONGOING:
                    return
                _record_livekit_join(session, student, event_at)
            else:
                _record_livekit_leave(session, student, event_at)
            return
    elif event.event.startswith('egress_') and event.HasField('egress_info'):
        _process_egress_event(event.egress_info)


def _process_egress_event(egress_info):
    session = TeachingSession.objects.filter(livekit_egress_id=egress_info.egress_id).first()
    if session is None:
        return
    verified_file = False
    verification_error = ''
    if egress_info.status == egress.EgressStatus.EGRESS_COMPLETE:
        try:
            # The Egress list response can omit file_results after completion.
            # The persisted key plus a private object read is the authoritative
            # check that this session's recording is actually playable.
            _verify_private_recording_object(session.recording_key)
            verified_file = True
        except Exception as exc:
            logger.exception('Completed Egress file could not be verified for session %s', session.pk)
            verification_error = f'Recording finished, but private storage retrieval failed ({type(exc).__name__}).'
    with transaction.atomic():
        session = TeachingSession.objects.select_for_update().get(pk=session.pk)
        if session.livekit_egress_id != egress_info.egress_id:
            return
        if (
            egress_info.status == egress.EgressStatus.EGRESS_ACTIVE
            and session.recording_status != TeachingSession.RecordingStatus.STOPPING
        ):
            session.recording_status = TeachingSession.RecordingStatus.RECORDING
        elif egress_info.status == egress.EgressStatus.EGRESS_COMPLETE:
            if verified_file:
                session.recording_status = TeachingSession.RecordingStatus.COMPLETED
                session.recording_error = ''
            else:
                session.recording_status = TeachingSession.RecordingStatus.FAILED
                session.recording_error = verification_error or 'The completed recording could not be read from private storage.'
        elif egress_info.status in (
            egress.EgressStatus.EGRESS_FAILED,
            egress.EgressStatus.EGRESS_ABORTED,
            egress.EgressStatus.EGRESS_LIMIT_REACHED,
        ):
            session.recording_status = TeachingSession.RecordingStatus.FAILED
            session.recording_error = 'LiveKit Egress did not complete successfully.'
        session.save(update_fields=['recording_status', 'recording_error'])


async def _get_egress_info(egress_id):
    client = api.LiveKitAPI(
        livekit_api_url(),
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    )
    try:
        response = await client.egress.list_egress(egress.ListEgressRequest(egress_id=egress_id))
        return next((item for item in response.items if item.egress_id == egress_id), None)
    finally:
        await client.aclose()


def refresh_livekit_recording_state(session):
    """Reconcile missed/delayed Egress webhooks against LiveKit's current job state."""
    if not session.livekit_egress_id:
        return session
    try:
        info = asyncio.run(_get_egress_info(session.livekit_egress_id))
    except Exception as exc:
        logger.warning(
            'Could not reconcile LiveKit Egress state for session %s (%s)',
            session.pk, type(exc).__name__,
        )
        return session
    if info is not None:
        _process_egress_event(info)
        session.refresh_from_db(fields=['recording_status', 'recording_error'])
    return session


def _recording_storage_client():
    import boto3
    from botocore.config import Config

    endpoint = settings.LIVEKIT_RECORDING_S3_ENDPOINT or None
    return boto3.client(
        's3',
        region_name=settings.LIVEKIT_RECORDING_S3_REGION,
        endpoint_url=endpoint,
        aws_access_key_id=settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
        aws_secret_access_key=settings.LIVEKIT_RECORDING_S3_SECRET,
        config=Config(s3={'addressing_style': 'path'}) if endpoint else None,
    )


def _verify_private_recording_object(key):
    """Verify that Egress actually wrote a retrievable object using private credentials."""
    if not key or not key.startswith('recordings/session-'):
        raise MarketError('Recording storage key is invalid.')
    client = _recording_storage_client()
    response = client.get_object(
        Bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
        Key=key,
        Range='bytes=0-0',
    )
    try:
        if not response['Body'].read(1):
            raise MarketError('Recording object is empty or not yet available.')
    finally:
        response['Body'].close()


async def _start_egress(session, recording_key):
    client = api.LiveKitAPI(
        livekit_api_url(),
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    )
    try:
        return await client.egress.start_room_composite_egress(
            egress.RoomCompositeEgressRequest(
                room_name=livekit_room_name(session),
                layout='grid',
                file=egress.EncodedFileOutput(
                    file_type=egress.EncodedFileType.MP4,
                    filepath=recording_key,
                    s3=egress.S3Upload(
                        bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
                        region=settings.LIVEKIT_RECORDING_S3_REGION,
                        endpoint=settings.LIVEKIT_RECORDING_S3_ENDPOINT,
                        access_key=settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
                        secret=settings.LIVEKIT_RECORDING_S3_SECRET,
                        force_path_style=bool(settings.LIVEKIT_RECORDING_S3_ENDPOINT),
                    ),
                ),
            )
        )
    finally:
        await client.aclose()


@transaction.atomic
def start_livekit_recording(session):
    if not livekit_recording_ready():
        raise MarketError(
            'Recording is unavailable. Configure LiveKit Egress and private S3 storage, then enable recording.'
        )
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    if session.status != TeachingSession.Status.ONGOING:
        raise MarketError('Recording can only start while the class is ongoing.')
    if session.recording_status in (
        TeachingSession.RecordingStatus.STARTING,
        TeachingSession.RecordingStatus.RECORDING,
    ):
        raise MarketError('A recording is already starting or active.')
    recording_key = f'recordings/session-{session.pk}/{uuid.uuid4().hex}.mp4'
    session.recording_status = TeachingSession.RecordingStatus.STARTING
    session.recording_key = recording_key
    session.recording_error = ''
    session.livekit_egress_id = ''
    session.save(update_fields=[
        'recording_status', 'recording_key', 'recording_error', 'livekit_egress_id',
    ])
    try:
        info = asyncio.run(_start_egress(session, recording_key))
    except (api.TwirpError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.exception('LiveKit Egress failed to start for session %s', session.pk)
        session.recording_status = TeachingSession.RecordingStatus.FAILED
        session.recording_error = 'LiveKit Egress could not start the recording. Check the LiveKit Egress configuration.'
        session.save(update_fields=['recording_status', 'recording_error'])
        raise MarketError('LiveKit Egress could not start the recording. Check server logs and Egress configuration.') from exc
    except Exception as exc:
        logger.exception('Unexpected LiveKit Egress start error for session %s', session.pk)
        session.recording_status = TeachingSession.RecordingStatus.FAILED
        session.recording_error = f'LiveKit Egress could not start ({type(exc).__name__}).'
        session.save(update_fields=['recording_status', 'recording_error'])
        raise MarketError('LiveKit Egress could not start the recording. Check server logs and Egress configuration.') from exc
    session.livekit_egress_id = info.egress_id
    session.save(update_fields=['livekit_egress_id'])
    return session


async def _stop_egress(egress_id):
    client = api.LiveKitAPI(
        livekit_api_url(),
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    )
    try:
        return await client.egress.stop_egress(egress.StopEgressRequest(egress_id=egress_id))
    finally:
        await client.aclose()


def stop_livekit_recording(session):
    with transaction.atomic():
        session = TeachingSession.objects.select_for_update().get(pk=session.pk)
        if session.recording_status not in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ) or not session.livekit_egress_id:
            raise MarketError('There is no active recording to stop.')
        already_stopping = session.recording_status == TeachingSession.RecordingStatus.STOPPING
        if not already_stopping:
            session.recording_status = TeachingSession.RecordingStatus.STOPPING
            session.save(update_fields=['recording_status'])

    if not already_stopping:
        try:
            asyncio.run(_stop_egress(session.livekit_egress_id))
        except (api.TwirpError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Egress may already have stopped automatically when the LiveKit
            # room ended. Reconcile before treating a repeated stop as failure.
            reconciled = refresh_livekit_recording_state(session)
            if reconciled.recording_status in (
                TeachingSession.RecordingStatus.COMPLETED,
                TeachingSession.RecordingStatus.FAILED,
            ):
                session = reconciled
            else:
                TeachingSession.objects.filter(pk=session.pk).update(
                    recording_status=TeachingSession.RecordingStatus.RECORDING,
                )
                logger.exception('LiveKit Egress failed to stop for session %s', session.pk)
                raise MarketError('LiveKit Egress could not stop the recording. Check server logs and Egress configuration.') from exc

    # Give Egress time to flush and upload its final MP4. The signed playback
    # gate remains closed unless the completion state and private read both pass.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        session = refresh_livekit_recording_state(session)
        if session.recording_status in (
            TeachingSession.RecordingStatus.COMPLETED,
            TeachingSession.RecordingStatus.FAILED,
        ):
            break
        time.sleep(2)
    return session


def livekit_recording_url(session):
    if not livekit_recording_ready() or session.recording_status != TeachingSession.RecordingStatus.COMPLETED:
        raise MarketError('No completed recording is available.')
    if not session.recording_key:
        raise MarketError('The completed recording has no storage reference.')
    from botocore.exceptions import BotoCoreError, ClientError
    client = _recording_storage_client()
    try:
        return client.generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.LIVEKIT_RECORDING_S3_BUCKET, 'Key': session.recording_key},
            ExpiresIn=300,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.exception('Could not generate private recording URL for session %s', session.pk)
        raise MarketError('The private recording could not be made available. Check recording storage configuration.') from exc


@transaction.atomic
def _record_livekit_join(session, student, event_at):
    attendance = SessionAttendance.objects.select_for_update().filter(
        session=session, student=student,
    ).first()
    if attendance is None:
        SessionAttendance.objects.create(
            session=session,
            student=student,
            first_joined_at=event_at,
            last_joined_at=event_at,
        )
    elif attendance.last_left_at is not None and event_at > attendance.last_left_at:
        attendance.join_count += 1
        attendance.last_joined_at = event_at
        attendance.last_left_at = None
        attendance.save(update_fields=['join_count', 'last_joined_at', 'last_left_at'])


@transaction.atomic
def _record_livekit_leave(session, student, event_at):
    attendance = SessionAttendance.objects.select_for_update().filter(
        session=session,
        student=student,
        last_left_at__isnull=True,
    ).first()
    if attendance is None or event_at < attendance.last_joined_at:
        return
    attendance.total_seconds += max(0, int((event_at - attendance.last_joined_at).total_seconds()))
    attendance.last_left_at = event_at
    attendance.save(update_fields=['total_seconds', 'last_left_at'])


def validate_upload(f):
    from django.conf import settings
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise MarketError('Unsupported file type.')
    if f.size > settings.MATERIAL_MAX_MB * 1024 * 1024:
        raise MarketError(f'File is larger than {settings.MATERIAL_MAX_MB} MB.')


def has_confirmed_payment(user, resource):
    return Payment.objects.filter(student=user, resource=resource, status=Payment.Status.CONFIRMED).exists()


def can_access_resource(user, resource) -> bool:
    """Single authorization point for every download. Server-side only."""
    if not user.is_authenticated:
        return False
    if resource.scan_status != Resource.ScanStatus.CLEAN:
        return False
    if resource.teacher_id == user.id:
        return True
    if not user.is_student:
        return False
    v = resource.visibility
    if v == Resource.Visibility.PUBLIC:
        return True
    if v == Resource.Visibility.CLASSES:
        return Enrollment.objects.filter(student=user, status=Enrollment.Status.ACTIVE,
                                         classroom__is_active=True, classroom__resources=resource).exists()
    if v == Resource.Visibility.PAID:
        return has_confirmed_payment(user, resource)
    return False        # draft


def request_purchase(student, resource):
    if resource.visibility != Resource.Visibility.PAID or not student.is_student:
        raise MarketError('This resource is not for sale.')
    if has_confirmed_payment(student, resource):
        raise MarketError('You already own this resource.')
    try:
        with transaction.atomic():
            return Payment.objects.create(student=student, teacher=resource.teacher, resource=resource,
                                          amount=resource.price)
    except IntegrityError:
        raise MarketError('You already have a purchase request for this resource.')


@transaction.atomic
def decide_payment(admin, payment_id, confirm: bool):
    p = Payment.objects.select_for_update().filter(pk=payment_id, status=Payment.Status.PENDING).first()
    if p is None:
        raise MarketError('Payment not found or already decided.')
    p.status = Payment.Status.CONFIRMED if confirm else Payment.Status.REJECTED
    p.confirmed_at, p.confirmed_by = timezone.now(), admin
    p.save(update_fields=['status', 'confirmed_at', 'confirmed_by'])
    if p.session_id:
        SessionBooking.objects.filter(session_id=p.session_id, student=p.student).update(
            status=SessionBooking.Status.CONFIRMED if confirm else SessionBooking.Status.CANCELLED)
    return p


@transaction.atomic
def book_session(student, session):
    if not student.is_student:
        raise MarketError('Only students can book sessions.')
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    sync_session_status(session)
    if session.status != TeachingSession.Status.UPCOMING or session.starts_at <= timezone.now():
        raise MarketError('This session is not open for booking.')
    live = session.bookings.exclude(status=SessionBooking.Status.CANCELLED)
    if live.filter(student=student).exists():
        raise MarketError('You already booked this session.')
    if live.count() >= session.capacity:
        raise MarketError('This session is full.')
    booking = SessionBooking.objects.filter(session=session, student=student).first()
    existing_payment = Payment.objects.filter(
        session=session, student=student, status=Payment.Status.CONFIRMED,
    ).exists()
    if session.price > 0 and not existing_payment:
        status = SessionBooking.Status.PENDING_PAYMENT
    else:
        status = SessionBooking.Status.CONFIRMED
    if booking is None:
        booking = SessionBooking.objects.create(session=session, student=student, status=status)
    else:
        booking.status = status
        booking.save(update_fields=['status'])
    if status == SessionBooking.Status.PENDING_PAYMENT:
        Payment.objects.create(student=student, teacher=session.teacher, session=session, amount=session.price)
    return booking


def sync_session_status(session, now=None):
    now = now or timezone.now()
    if session.status in (TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING) and session.ends_at <= now:
        session.status = TeachingSession.Status.COMPLETED
        session.save(update_fields=['status'])
    return session


@transaction.atomic
def start_session(session):
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    sync_session_status(session)
    if session.status != TeachingSession.Status.UPCOMING:
        raise MarketError('Only upcoming sessions can be started.')
    if session.ends_at <= timezone.now():
        raise MarketError('This session has already ended.')
    session.status = TeachingSession.Status.ONGOING
    session.save(update_fields=['status'])
    return session


@transaction.atomic
def end_session(session):
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    if session.status != TeachingSession.Status.ONGOING:
        raise MarketError('Only ongoing sessions can be ended.')
    session.status = TeachingSession.Status.COMPLETED
    session.save(update_fields=['status'])
    return session


@transaction.atomic
def cancel_session(session):
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    if session.status not in (TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING):
        raise MarketError('This session can no longer be cancelled.')
    session.status = TeachingSession.Status.CANCELLED
    session.save(update_fields=['status'])
    session.bookings.exclude(status=SessionBooking.Status.CANCELLED).update(
        status=SessionBooking.Status.CANCELLED,
    )
    Payment.objects.filter(session=session, status=Payment.Status.PENDING).update(
        status=Payment.Status.REJECTED,
        confirmed_at=timezone.now(),
    )
    return session


@transaction.atomic
def cancel_session_booking(student, session):
    session = TeachingSession.objects.select_for_update().get(pk=session.pk)
    booking = SessionBooking.objects.select_for_update().filter(
        session=session, student=student,
    ).exclude(status=SessionBooking.Status.CANCELLED).first()
    if booking is None:
        raise MarketError('No active booking was found.')
    if session.status != TeachingSession.Status.UPCOMING or session.starts_at <= timezone.now():
        raise MarketError('Bookings can only be cancelled before the session starts.')
    booking.status = SessionBooking.Status.CANCELLED
    booking.save(update_fields=['status'])
    Payment.objects.filter(
        session=session, student=student, status=Payment.Status.PENDING,
    ).update(status=Payment.Status.REJECTED, confirmed_at=timezone.now())
    return booking


def notify_session_bookings(session, subject, body):
    recipients = list(session.bookings.filter(
        status__in=(SessionBooking.Status.CONFIRMED, SessionBooking.Status.PENDING_PAYMENT),
    ).values_list('student__email', flat=True))
    recipients = [email for email in recipients if email]
    if recipients and settings.EMAIL_HOST:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)


def notify_booking(booking):
    if not settings.EMAIL_HOST:
        return
    status_text = booking.get_status_display()
    scheduled_for = timezone.localtime(booking.session.starts_at).strftime('%Y-%m-%d %H:%M')
    if booking.student.email:
        send_mail(
            f'Booking {status_text.lower()}: {booking.session.title}',
            f'Your booking for {booking.session.title} on {scheduled_for} is {status_text.lower()}.',
            settings.DEFAULT_FROM_EMAIL,
            [booking.student.email],
        )
    if booking.session.teacher.email:
        send_mail(
            f'New student booking: {booking.session.title}',
            f'{booking.student.get_full_name() or booking.student.username} has a {status_text.lower()} '
            f'booking for {booking.session.title} on {scheduled_for}.',
            settings.DEFAULT_FROM_EMAIL,
            [booking.session.teacher.email],
        )


def notify_booking_cancelled(student, session):
    if settings.EMAIL_HOST and session.teacher.email:
        send_mail(
            f'Booking cancelled: {session.title}',
            f'{student.get_full_name() or student.username} cancelled their booking for {session.title}.',
            settings.DEFAULT_FROM_EMAIL,
            [session.teacher.email],
        )


def send_booking_reminders(student):
    if not settings.EMAIL_HOST or not student.email:
        return
    now = timezone.now()
    bookings = SessionBooking.objects.filter(
        student=student,
        status=SessionBooking.Status.CONFIRMED,
        reminder_sent_at__isnull=True,
        session__status=TeachingSession.Status.UPCOMING,
        session__starts_at__gt=now,
        session__starts_at__lte=now + timedelta(hours=24),
    ).select_related('session')
    for booking in bookings:
        send_mail(
            f'Class reminder: {booking.session.title}',
            f'Reminder: {booking.session.title} starts at '
            f'{timezone.localtime(booking.session.starts_at):%Y-%m-%d %H:%M}.',
            settings.DEFAULT_FROM_EMAIL,
            [student.email],
        )
        booking.reminder_sent_at = now
        booking.save(update_fields=['reminder_sent_at'])


def can_review(student, teacher) -> bool:
    return student.is_student and (
        Enrollment.objects.filter(student=student, classroom__teacher=teacher, status=Enrollment.Status.ACTIVE).exists()
        or SessionBooking.objects.filter(student=student, session__teacher=teacher,
                                         status=SessionBooking.Status.CONFIRMED).exists())
