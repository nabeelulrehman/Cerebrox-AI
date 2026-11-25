import mimetypes
import os
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from botocore.exceptions import ClientError

from apps.accounts.permissions import admin_required, verified_teacher_required
from apps.classrooms.models import Enrollment
from apps.teachers.models import TeacherProfile
from apps.teachers.models import TeacherPaymentMethod
from . import services
from .forms import (
    GuidanceComposeForm, GuidanceForm, GuidanceReplyForm, ResourceForm, ResourceMetadataForm,
    ReviewForm, SessionForm,
)
from .models import (
    GuidanceMessage, Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking,
    SessionAttendance, Review, PendingObjectUpload,
)
from .services import MarketError
from .uploads import issue_upload_url, snapshot_uploaded_object, validate_resource_content, verify_uploaded_object
from .storage import object_storage_client


def _student(request):
    if not request.user.is_student:
        raise PermissionDenied


def _approved_teacher_or_404(teacher_profile_id):
    return get_object_or_404(TeacherProfile.objects.select_related('user'), pk=teacher_profile_id,
                             verification_status=TeacherProfile.Status.APPROVED)


# ---------------- materials ----------------

def public_resources(request):
    qs = Resource.objects.filter(visibility__in=[Resource.Visibility.PUBLIC, Resource.Visibility.PAID],
                                 scan_status=Resource.ScanStatus.CLEAN,
                                 teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED)
    return render(request, 'marketplace/resources.html', {'resources': qs.select_related('teacher')})


@verified_teacher_required
def teacher_resources(request):
    if settings.USE_OBJECT_STORAGE:
        form = ResourceMetadataForm(request.POST or None, teacher=request.user)
        return render(request, 'marketplace/teacher_resources.html', {
            'form': form,
            'resources': Resource.objects.filter(teacher=request.user),
            'direct_upload': True,
            'MATERIAL_MAX_MB': settings.MATERIAL_MAX_MB,
        })

    form = ResourceForm(request.POST or None, request.FILES or None, teacher=request.user)
    if request.method == 'POST' and form.is_valid():
        r = form.save(commit=False)
        r.teacher, r.original_name = request.user, form.cleaned_data['file'].name[:255]
        r.save()
        form.save_m2m()
        messages.success(request, 'Resource saved.')
        return redirect('market:teacher_resources')
    return render(request, 'marketplace/teacher_resources.html',
                  {'form': form, 'resources': Resource.objects.filter(teacher=request.user), 'direct_upload': False})


@verified_teacher_required
@require_POST
def resource_upload_start(request):
    if not settings.USE_OBJECT_STORAGE:
        return JsonResponse({'error': 'Persistent object storage is not configured.'}, status=503)

    filename = os.path.basename(request.POST.get('file_name', '').replace('\\', '/')).strip()
    try:
        size = int(request.POST.get('file_size', ''))
    except (TypeError, ValueError):
        size = 0
    if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
        return JsonResponse({'error': 'Choose a valid file name.'}, status=400)
    if size <= 0 or size > settings.MATERIAL_MAX_MB * 1024 * 1024:
        return JsonResponse({'error': f'Choose a file between 1 byte and {settings.MATERIAL_MAX_MB} MB.'}, status=400)
    try:
        services.validate_upload(type('UploadMetadata', (), {'name': filename, 'size': size})())
    except MarketError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    form = ResourceMetadataForm(request.POST, teacher=request.user)
    if not form.is_valid():
        return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
    pending = PendingObjectUpload.objects.filter(
        owner=request.user, purpose=PendingObjectUpload.Purpose.RESOURCE,
        expires_at__gt=timezone.now(),
    )
    if pending.count() >= 5:
        return JsonResponse({'error': 'Finish or wait for an existing upload to expire before starting another.'}, status=429)

    extension = os.path.splitext(filename)[1].lower()
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    metadata = {
        'title': form.cleaned_data['title'],
        'description': form.cleaned_data['description'],
        'visibility': form.cleaned_data['visibility'],
        'price': str(form.cleaned_data['price']) if form.cleaned_data['price'] is not None else '',
        'classrooms': [str(classroom.pk) for classroom in form.cleaned_data['classrooms']],
    }
    upload = PendingObjectUpload.objects.create(
        owner=request.user,
        purpose=PendingObjectUpload.Purpose.RESOURCE,
        object_key=f'upload-staging/resources/{uuid.uuid4().hex}{extension}',
        final_key=f'teacher-resources/{uuid.uuid4().hex}{extension}',
        original_name=filename,
        content_type=content_type,
        size=size,
        metadata=metadata,
    )
    try:
        response = issue_upload_url(upload)
    except Exception:
        upload.delete()
        return JsonResponse({'error': 'Could not prepare secure storage upload. Try again later.'}, status=503)
    return JsonResponse(response)


@verified_teacher_required
@require_POST
def resource_upload_finish(request):
    if not settings.USE_OBJECT_STORAGE:
        return JsonResponse({'error': 'Persistent object storage is not configured.'}, status=503)
    try:
        upload_id = uuid.UUID(request.POST.get('upload_id', ''))
    except (TypeError, ValueError, AttributeError):
        return JsonResponse({'error': 'Invalid upload authorization.'}, status=400)
    upload = PendingObjectUpload.objects.filter(
        pk=upload_id, owner=request.user, purpose=PendingObjectUpload.Purpose.RESOURCE,
        resource__isnull=True,
    ).first()
    if upload is None:
        return JsonResponse({'error': 'Upload authorization was not found or has already been used.'}, status=404)
    try:
        verify_uploaded_object(upload)
        snapshot_uploaded_object(upload)
        validate_resource_content(upload)
    except ValueError as exc:
        storage_cleanup_succeeded = False
        try:
            from .uploads import delete_uploaded_object
            delete_uploaded_object(upload)
            storage_cleanup_succeeded = True
        except Exception:
            # Keep the expired authorization row; the cleanup command retries both keys.
            pass
        if storage_cleanup_succeeded:
            upload.delete()
        return JsonResponse({'error': str(exc)}, status=400)
    except ClientError as exc:
        code = str(exc.response.get('Error', {}).get('Code', ''))
        if code in {'404', 'NoSuchKey', 'NotFound'}:
            return JsonResponse({'error': 'Upload is not available yet. Wait for it to finish and try again.'}, status=409)
        return JsonResponse({'error': 'The uploaded object could not be verified. Try again later.'}, status=503)
    except Exception:
        return JsonResponse({'error': 'The uploaded object could not be verified. Try again later.'}, status=503)

    form = ResourceMetadataForm(upload.metadata, teacher=request.user)
    if not form.is_valid():
        return JsonResponse({'error': 'Resource details are no longer valid; start a new upload.',
                             'errors': form.errors.get_json_data()}, status=400)
    try:
        with transaction.atomic():
            upload = PendingObjectUpload.objects.select_for_update().get(
                pk=upload.pk, owner=request.user,
                purpose=PendingObjectUpload.Purpose.RESOURCE,
                expires_at__gt=timezone.now(),
            )
            resource = form.save(commit=False)
            resource.teacher = request.user
            resource.file.name = upload.scan_key
            resource.original_name = upload.original_name
            resource.scan_status = Resource.ScanStatus.PENDING
            resource.save()
            form.save_m2m()
            upload.resource = resource
            upload.save(update_fields=['resource'])
    except PendingObjectUpload.DoesNotExist:
        return JsonResponse({'error': 'Upload authorization expired or was already used.'}, status=404)
    return JsonResponse({
        'ok': True,
        'resource_id': resource.pk,
        'scan_status': Resource.ScanStatus.PENDING,
        'message': 'Upload received. The file will be available after the security scan passes.',
    }, status=202)


@verified_teacher_required
@require_POST
def resource_delete(request, resource_id):
    r = get_object_or_404(Resource, pk=resource_id, teacher=request.user)
    try:
        with transaction.atomic():
            r = Resource.objects.select_for_update().get(pk=r.pk, teacher=request.user)
            r.file.delete(save=False)
            r.delete()
    except Exception:
        messages.error(request, 'The resource could not be deleted from private storage. Please retry.')
        return redirect('market:teacher_resources')
    messages.info(request, 'Resource deleted.')
    return redirect('market:teacher_resources')


@login_required
def resource_download(request, resource_id):
    r = get_object_or_404(Resource, pk=resource_id)
    if not services.can_access_resource(request.user, r):
        raise Http404            # 404, not 403: don't reveal that a private file exists
    if settings.USE_OBJECT_STORAGE:
        from urllib.parse import quote

        client = object_storage_client()
        safe_filename = (r.original_name or 'download').replace('\\', '_').replace('/', '_').replace('"', '_').replace('\r', '').replace('\n', '')
        download_url = client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.OBJECT_STORAGE_BUCKET,
                'Key': r.file.storage._normalize_name(r.file.name),
                'ResponseContentDisposition': f"attachment; filename*=UTF-8''{quote(safe_filename)}",
                'ResponseContentType': 'application/octet-stream',
            },
            ExpiresIn=300,
            HttpMethod='GET',
        )
        response = redirect(download_url)
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response
    resp = FileResponse(r.file.open('rb'), as_attachment=True, filename=r.original_name or 'download')
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'private, no-store'
    return resp


@login_required
@require_POST
def resource_buy(request, resource_id):
    _student(request)
    r = get_object_or_404(
        Resource, pk=resource_id, visibility=Resource.Visibility.PAID,
        scan_status=Resource.ScanStatus.CLEAN,
    )
    try:
        services.request_purchase(request.user, r)
    except MarketError as exc:
        if 'already own' not in str(exc).lower() and 'already have' not in str(exc).lower():
            messages.error(request, str(exc))
    return redirect('market:teacher_payment_checkout', kind='resource', target_id=r.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def teacher_payment_checkout(request, kind, target_id):
    _student(request)
    if kind == 'resource':
        target = get_object_or_404(Resource, pk=target_id, visibility=Resource.Visibility.PAID,
                                   scan_status=Resource.ScanStatus.CLEAN)
        if services.has_confirmed_payment(request.user, target):
            messages.info(request, 'You already have access to this resource.')
            return redirect('market:resources')
        resource, session = target, None
    elif kind == 'session':
        return redirect('market:session_checkout', session_id=target_id)
    elif kind == 'classroom':
        from apps.classrooms.models import Classroom
        target = get_object_or_404(Classroom, pk=target_id, is_active=True,
                                   teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED)
        if (request.session.get('pending_paid_class_code') != target.join_code
                or target.price <= 0 or target.enrollment_policy == Classroom.Policy.INVITE_ONLY):
            raise Http404
        if target.enrollments.filter(student=request.user, status='active').exists():
            messages.info(request, 'You already have access to this class.')
            return redirect('classrooms:my_classes')
        resource, session = None, None
    else:
        raise Http404
    methods = TeacherPaymentMethod.objects.filter(teacher=target.teacher, is_active=True)
    if request.method == 'POST':
        selected = methods.filter(pk=request.POST.get('payment_method')).first()
        try:
            payment = services.submit_teacher_payment(request.user, resource=resource, session=session,
                classroom=target if kind == 'classroom' else None,
                payment_method=selected, transaction_reference=request.POST.get('transaction_reference', ''),
                receipt=request.FILES.get('receipt'))
            if kind == 'classroom':
                request.session.pop('pending_paid_class_code', None)
            messages.success(request, 'Payment submitted to the teacher for verification. Access is granted after approval.')
            return redirect('market:payment_result', payment_reference=payment.reference)
        except MarketError as exc:
            messages.error(request, str(exc))
    return render(request, 'marketplace/teacher_payment_checkout.html', {
        'target': target, 'target_title': getattr(target, 'title', getattr(target, 'name', '')),
        'kind': kind, 'methods': methods,
    })


# ---------------- guidance ----------------

@login_required
@require_POST
def guidance_request(request, teacher_id):
    _student(request)
    t = _approved_teacher_or_404(teacher_id)
    form = GuidanceForm(request.POST)
    if form.is_valid():
        g = form.save(commit=False)
        g.student, g.teacher, g.started_by = request.user, t.user, request.user
        g.save()
        messages.success(request, 'Your message was sent. The teacher can reply here in the app.')
    return redirect('teachers:public_profile', teacher_id=t.id)


def _guidance_context(user, form=None):
    if user.is_student:
        conversations = GuidanceRequest.objects.filter(student=user)
    elif user.is_verified_teacher:
        conversations = GuidanceRequest.objects.filter(teacher=user)
    else:
        raise PermissionDenied
    conversations = conversations.select_related(
        'student', 'teacher', 'started_by',
    ).prefetch_related(Prefetch(
        'messages',
        queryset=GuidanceMessage.objects.select_related('sender').order_by('sent_at', 'pk'),
    ))
    return {
        'items': conversations,
        'compose_form': form if form is not None else GuidanceComposeForm(user=user),
        'selected_recipient': form.cleaned_data.get('recipient') if form and form.is_bound else None,
        'is_teacher': user.is_verified_teacher,
    }


def _render_guidance(request, form=None, status=200):
    template = 'marketplace/inbox.html' if request.user.is_verified_teacher else 'marketplace/my_guidance.html'
    return render(request, template, _guidance_context(request.user, form), status=status)


@login_required
@require_GET
def guidance_recipient_search(request):
    user = request.user
    if not user.is_student and not user.is_verified_teacher:
        raise PermissionDenied

    query = request.GET.get('q', '').strip()[:100]
    matches = GuidanceComposeForm.recipient_queryset(user)
    if len(query) < 2:
        matches = matches.none()
    else:
        for term in query.split():
            matches = matches.filter(
                Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(username__icontains=term)
                | Q(email__icontains=term)
            )
    results = []
    for recipient in matches[:10]:
        results.append({
            'id': recipient.pk,
            'label': recipient.get_full_name() or recipient.username,
            'detail': f'@{recipient.username}' + (f' · {recipient.email}' if recipient.email else ''),
        })
    return JsonResponse({'results': results})


@verified_teacher_required
def teacher_inbox(request):
    return _render_guidance(request)


@login_required
@require_POST
def guidance_send(request):
    if not request.user.is_student and not request.user.is_verified_teacher:
        if request.user.is_teacher:
            messages.warning(request, 'Your teacher account has not been approved yet.')
            return redirect('teachers:dashboard')
        raise PermissionDenied

    form = GuidanceComposeForm(request.POST, user=request.user)
    if not form.is_valid():
        return _render_guidance(request, form=form, status=400)

    recipient = form.cleaned_data['recipient']
    if request.user.is_student:
        student, teacher = request.user, recipient
    else:
        student, teacher = recipient, request.user
    conversation = GuidanceRequest.objects.create(
        student=student,
        teacher=teacher,
        subject=form.cleaned_data['subject'],
        message=form.cleaned_data['message'],
        started_by=request.user,
    )
    messages.success(request, 'Your message was sent.')
    return redirect(f"{reverse('market:my_guidance')}#conversation-{conversation.pk}")


def _add_guidance_reply(request, conversation, message_text):
    form = GuidanceReplyForm({'message': message_text})
    if not form.is_valid():
        messages.error(request, 'Enter a message of up to 2,000 characters before sending.')
        return False
    GuidanceMessage.objects.create(
        conversation=conversation,
        sender=request.user,
        body=form.cleaned_data['message'],
    )
    conversation.status = (
        GuidanceRequest.Status.ANSWERED if request.user.is_teacher else GuidanceRequest.Status.OPEN
    )
    conversation.save(update_fields=['status'])
    messages.success(request, 'Your reply was sent.')
    return True


@verified_teacher_required
@require_POST
def guidance_reply(request, request_id):
    g = get_object_or_404(GuidanceRequest, pk=request_id, teacher=request.user)
    _add_guidance_reply(request, g, request.POST.get('message', request.POST.get('reply', '')))
    return redirect(f"{reverse('market:teacher_inbox')}#conversation-{g.pk}")


@login_required
@require_POST
def guidance_conversation_reply(request, request_id):
    user = request.user
    if not user.is_student and not user.is_verified_teacher:
        raise PermissionDenied
    conversation = get_object_or_404(
        GuidanceRequest.objects.filter(Q(student=user) | Q(teacher=user)),
        pk=request_id,
    )
    _add_guidance_reply(request, conversation, request.POST.get('message', ''))
    return redirect(f"{reverse('market:my_guidance')}#conversation-{conversation.pk}")


@login_required
def my_guidance(request):
    return _render_guidance(request)


# ---------------- sessions ----------------

def _teacher_session_list(teacher):
    sessions = list(TeachingSession.objects.filter(teacher=teacher).annotate(
        payment_count=Count('payments', distinct=True),
        attendance_count=Count('attendance_records', distinct=True),
    ).prefetch_related('bookings').order_by('-starts_at'))
    for session in sessions:
        session.booking_count = session.bookings.count()
        session.has_related_records = bool(
            session.booking_count or session.payment_count or session.attendance_count
            or session.classroom_id or session.recording_key or session.livekit_egress_id
            or session.recording_status not in (
                TeachingSession.RecordingStatus.UNAVAILABLE,
                TeachingSession.RecordingStatus.IDLE,
            )
            or session.status != TeachingSession.Status.UPCOMING
        )
    return sessions


@verified_teacher_required
def teacher_sessions(request):
    now = timezone.now()
    for session in TeachingSession.objects.filter(
        teacher=request.user,
        status__in=(TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING),
        starts_at__lte=now,
    ):
        services.sync_session_status(session, now)
    sessions = _teacher_session_list(request.user)
    for session in sessions:
        if session.livekit_egress_id and session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.STOPPING,
            TeachingSession.RecordingStatus.RECORDING,
        ):
            services.refresh_livekit_recording_state(session)
    form = SessionForm(request.POST or None, teacher=request.user)
    if request.method == 'POST' and form.is_valid():
        s = form.save(commit=False)
        s.teacher = request.user
        s.save()
        messages.success(request, 'Session created.')
        return redirect('market:session_detail', session_id=s.pk)
    return render(request, 'marketplace/teacher_sessions.html', {
        'form': form,
        'sessions': sessions,
        'recording_ready': services.livekit_recording_ready(),
    })


@login_required
def sessions_list(request):
    _student(request)
    now = timezone.now()
    services.send_booking_reminders(request.user)
    for session in TeachingSession.objects.filter(
        status__in=(TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING),
        starts_at__lte=now,
    ):
        services.sync_session_status(session, now)
    bookings = SessionBooking.objects.filter(student=request.user).select_related('session')
    mine = {booking.session_id: booking for booking in bookings}
    attendance_by_session = {
        record.session_id: record
        for record in SessionAttendance.objects.filter(student=request.user)
    }
    sessions = TeachingSession.objects.filter(
        Q(status=TeachingSession.Status.ONGOING)
        | Q(status=TeachingSession.Status.UPCOMING, starts_at__gt=now)
        | Q(bookings__student=request.user),
    ).filter(
        Q(teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED)
        | Q(bookings__student=request.user),
    ).filter(
        Q(access_type__in=(TeachingSession.AccessType.PUBLIC_ONLY, TeachingSession.AccessType.CLASS_PUBLIC))
        | Q(classroom__enrollments__student=request.user,
            classroom__enrollments__status=Enrollment.Status.ACTIVE,
            classroom__is_active=True),
    ).select_related('teacher', 'teacher__teacher_profile').distinct().order_by('starts_at')
    sessions = list(sessions)
    for session in sessions:
        services.sync_session_status(session, now)
        session.student_booking = mine.get(session.pk)
        session.student_attendance = attendance_by_session.get(session.pk)
        session.reminder_due = (
            session.status == TeachingSession.Status.UPCOMING
            and now < session.starts_at <= now + timedelta(hours=24)
            and session.student_booking is not None
            and session.student_booking.status == SessionBooking.Status.CONFIRMED
        )
    sessions = [
        session for session in sessions
        if session.status in (TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING)
        or session.pk in mine
    ]
    return render(request, 'marketplace/sessions.html', {'sessions': sessions})


@login_required
def session_detail(request, session_id):
    session = get_object_or_404(
        TeachingSession.objects.select_related('teacher', 'teacher__teacher_profile'),
        pk=session_id,
    )
    booking = SessionBooking.objects.filter(session=session, student=request.user).first()
    owns_session = request.user.is_teacher and session.teacher_id == request.user.pk
    if not owns_session:
        _student(request)
        class_student = services.is_session_class_student(request.user, session)
        if session.access_type == TeachingSession.AccessType.CLASS_ONLY and not class_student:
            raise Http404
        if (
            not hasattr(session.teacher, 'teacher_profile')
            or session.teacher.teacher_profile.verification_status != TeacherProfile.Status.APPROVED
        ) and booking is None:
            raise Http404
    services.sync_session_status(session)
    if request.user.is_student:
        services.send_booking_reminders(request.user)
    active_booking = booking and booking.status != SessionBooking.Status.CANCELLED
    can_manage = request.user.is_teacher and session.teacher_id == request.user.pk
    if can_manage and session.livekit_egress_id and session.recording_status in (
        TeachingSession.RecordingStatus.STARTING,
        TeachingSession.RecordingStatus.STOPPING,
        TeachingSession.RecordingStatus.RECORDING,
    ):
        session = services.refresh_livekit_recording_state(session)
    attendance = None
    bookings = None
    if can_manage:
        bookings = session.bookings.select_related('student').order_by('created_at')
        attendance = session.attendance_records.select_related('student').all()
    elif booking:
        attendance = SessionAttendance.objects.filter(session=session, student=request.user).first()
    class_student = bool(request.user.is_student and services.is_session_class_student(request.user, session))
    class_booking_upgrade = bool(
        class_student and booking and booking.status == SessionBooking.Status.PENDING_PAYMENT
    )
    can_book = (
        request.user.is_student
        and (not active_booking or class_booking_upgrade)
        and (session.status == TeachingSession.Status.UPCOMING and session.starts_at > timezone.now()
             or services.is_session_class_student(request.user, session)
             and session.status == TeachingSession.Status.ONGOING)
        and (services.is_session_class_student(request.user, session) or session.access_type in (
            TeachingSession.AccessType.PUBLIC_ONLY, TeachingSession.AccessType.CLASS_PUBLIC,
        ))
        and session.bookings.exclude(status=SessionBooking.Status.CANCELLED).count() < session.capacity
    )
    can_cancel_booking = (
        request.user.is_student
        and active_booking
        and session.status == TeachingSession.Status.UPCOMING
        and session.starts_at > timezone.now()
    )
    reminder_due = (
        session.status == TeachingSession.Status.UPCOMING
        and timezone.now() < session.starts_at <= timezone.now() + timedelta(hours=24)
    )
    return render(request, 'marketplace/session_detail.html', {
        'session': session,
        'booking': booking,
        'active_booking': active_booking,
        'can_manage': can_manage,
        'class_student': class_student,
        'can_view_recording': can_manage or bool(
            request.user.is_student and booking and booking.status == SessionBooking.Status.CONFIRMED
            and (class_student or session.price <= 0 or Payment.objects.filter(
                session=session, student=request.user, status=Payment.Status.CONFIRMED,
            ).exists())
        ),
        'bookings': bookings,
        'attendance': attendance,
        'can_book': can_book,
        'can_cancel_booking': can_cancel_booking,
        'can_join': bool(
            request.user.is_student
            and active_booking
            and booking.status == SessionBooking.Status.CONFIRMED
            and (session.status == TeachingSession.Status.ONGOING or (
                class_student and session.status == TeachingSession.Status.UPCOMING
            ))
        ),
        'recording_ready': services.livekit_recording_ready(),
        'reminder_due': reminder_due,
    })


def _classroom_role(request, session):
    services.sync_session_status(session)
    if request.user.is_teacher and session.teacher_id == request.user.pk:
        if session.status != TeachingSession.Status.ONGOING:
            raise MarketError('The teacher has not started this class, or it has already ended.')
        return True
    class_student = request.user.is_student and services.is_session_class_student(request.user, session)
    if session.status != TeachingSession.Status.ONGOING and not (
        class_student and session.status == TeachingSession.Status.UPCOMING
        and session.starts_at > timezone.now()
    ):
        raise MarketError('This class is not currently open.')
    if (request.user.is_student
            and session.access_type == TeachingSession.AccessType.CLASS_ONLY
            and not services.is_session_class_student(request.user, session)):
        raise PermissionDenied
    if class_student:
        # Enrollment grants free class access, but still requires an ordinary
        # confirmed booking so capacity and attendance remain accounted for.
        if session.bookings.filter(student=request.user, status=SessionBooking.Status.CONFIRMED).exists():
            return False
    if request.user.is_student and session.bookings.filter(
        student=request.user, status=SessionBooking.Status.CONFIRMED,
    ).exists():
        if session.price <= 0 or Payment.objects.filter(
            session=session, student=request.user, status=Payment.Status.CONFIRMED,
        ).exists():
            return False
    raise PermissionDenied


@login_required
def session_classroom(request, session_id):
    session = get_object_or_404(
        TeachingSession.objects.select_related('teacher'),
        pk=session_id,
    )
    try:
        is_teacher = _classroom_role(request, session)
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)
    return render(request, 'marketplace/classroom.html', {
        'session': session,
        'is_teacher': is_teacher,
        'teacher_identity': services.livekit_participant_identity(session, session.teacher),
        'livekit_ready': services.livekit_ready(),
        'livekit_configuration_issues': services.livekit_configuration_issues(),
        'recording_ready': services.livekit_recording_ready(),
        'recording_issues': services.livekit_recording_configuration_issues(),
        'recording_status': session.get_recording_status_display(),
        'recording_error': session.recording_error if is_teacher else '',
    })


@login_required
@require_POST
def session_token(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        is_teacher = _classroom_role(request, session)
        token = services.issue_livekit_token(session, request.user, is_teacher=is_teacher)
    except MarketError as exc:
        return JsonResponse({'error': str(exc)}, status=503 if not services.livekit_ready() else 403)
    return JsonResponse({
        'url': settings.LIVEKIT_URL,
        'token': token,
    }, headers={'Cache-Control': 'private, no-store'})


@csrf_exempt
@require_POST
def livekit_webhook(request):
    try:
        event = services.parse_livekit_webhook(request.body, request.headers.get('Authorization', ''))
    except MarketError:
        return JsonResponse({'error': 'Invalid webhook request.'}, status=401)
    services.process_livekit_webhook(event)
    return HttpResponse(status=200)


@verified_teacher_required
@require_POST
def session_recording_start(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    if request.POST.get('recording_consent') != 'on':
        messages.error(request, 'Confirm that everyone in the class has been told and has agreed before recording.')
        return redirect('market:session_classroom', session_id=session.pk)
    try:
        services.start_livekit_recording(session)
        messages.success(request, 'Recording is starting. The status will update when LiveKit Egress confirms it.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_classroom', session_id=session.pk)


@login_required
@require_GET
def session_recording_status(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    authorized = (
        request.user.is_teacher and session.teacher_id == request.user.pk
        or request.user.is_student and session.bookings.filter(
            student=request.user, status=SessionBooking.Status.CONFIRMED,
        ).exists()
    )
    if not authorized:
        raise PermissionDenied
    if (
        request.user.is_teacher
        and session.teacher_id == request.user.pk
        and session.livekit_egress_id
        and session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.STOPPING,
        )
    ):
        session = services.refresh_livekit_recording_state(session)
    return JsonResponse({
        'status': session.recording_status,
        'label': session.get_recording_status_display(),
        'error': session.recording_error if request.user.is_teacher else '',
        'recording_ready': services.livekit_recording_ready(),
        'can_control': bool(request.user.is_teacher and session.teacher_id == request.user.pk),
    }, headers={'Cache-Control': 'private, no-store'})


@verified_teacher_required
@require_POST
def session_recording_stop(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        services.stop_livekit_recording(session)
        messages.success(request, 'Recording is stopping. It will be available after LiveKit finishes the file.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_classroom', session_id=session.pk)


@login_required
def session_recording_play(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    class_access = services.is_session_class_student(request.user, session)
    if not (
        request.user.is_teacher and session.teacher_id == request.user.pk
        or request.user.is_student and (class_access or session.access_type != TeachingSession.AccessType.CLASS_ONLY) and session.bookings.filter(
            student=request.user, status=SessionBooking.Status.CONFIRMED,
        ).exists() and (session.price <= 0 or Payment.objects.filter(
            session=session, student=request.user, status=Payment.Status.CONFIRMED,
        ).exists())
    ):
        raise PermissionDenied
    try:
        return redirect(services.livekit_recording_url(session))
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
def session_edit(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    services.sync_session_status(session)
    if session.status != TeachingSession.Status.UPCOMING:
        messages.error(request, 'Only upcoming sessions can be edited or rescheduled.')
        return redirect('market:session_detail', session_id=session.pk)
    original_schedule = (session.starts_at, session.duration_minutes)
    form = SessionForm(request.POST or None, instance=session, teacher=request.user)
    if request.method == 'POST' and form.is_valid():
        session = form.save(commit=False)
        session.save()
        if original_schedule != (session.starts_at, session.duration_minutes):
            services.notify_session_bookings(
                session,
                f'Session rescheduled: {session.title}',
                f'{session.title} has been rescheduled to '
                f'{timezone.localtime(session.starts_at):%Y-%m-%d %H:%M} '
                f'and will end at {timezone.localtime(session.ends_at):%H:%M}.',
            )
            messages.success(request, 'Session rescheduled; booked students were notified by email where configured.')
        else:
            messages.success(request, 'Session details updated.')
        return redirect('market:session_detail', session_id=session.pk)
    return render(request, 'marketplace/teacher_sessions.html', {
        'form': form,
        'session': session,
        'sessions': _teacher_session_list(request.user),
    })


@verified_teacher_required
@require_POST
def session_delete(request, session_id):
    try:
        with transaction.atomic():
            session = get_object_or_404(
                TeachingSession.objects.select_for_update(), pk=session_id, teacher=request.user,
            )
            title = session.title
            starts_at = session.starts_at
            recipients = list(session.bookings.filter(
                status__in=(SessionBooking.Status.CONFIRMED, SessionBooking.Status.PENDING_PAYMENT),
            ).exclude(student__email='').values_list('student__email', flat=True))
            if session.recording_status in (
                TeachingSession.RecordingStatus.STARTING,
                TeachingSession.RecordingStatus.RECORDING,
                TeachingSession.RecordingStatus.STOPPING,
            ):
                session = services.stop_livekit_recording(session)
            if session.recording_key:
                services.delete_livekit_recording(session)

            # Detach payments before cascading session-owned bookings and attendance.
            Payment.objects.filter(session=session).update(session_title=title, session=None)
            session.delete()
            if recipients and settings.EMAIL_HOST:
                transaction.on_commit(lambda: services.notify_session_deletion(title, starts_at, recipients))
        messages.success(request, f'Session {title} was permanently deleted. Payment and audit history was retained.')
    except MarketError as exc:
        messages.error(request, f'The session was not deleted: {exc}')
    return redirect('market:teacher_sessions')


@verified_teacher_required
@require_POST
def session_start(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        services.start_session(session)
        services.notify_session_bookings(
            session, f'Class is starting: {session.title}',
            f'{session.title} is now ongoing. Open your booking to join the class.',
        )
        messages.success(request, 'Class started. Confirmed students can now join.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
@require_POST
def session_end(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        if session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ):
            services.stop_livekit_recording(session)
        services.end_session(session)
        messages.success(request, 'Class ended and marked as completed.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
@require_POST
def session_cancel(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        if session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ):
            services.stop_livekit_recording(session)
        services.notify_session_bookings(
            session, f'Session cancelled: {session.title}',
            f'{session.title} scheduled for {timezone.localtime(session.starts_at):%Y-%m-%d %H:%M} has been cancelled.',
        )
        services.cancel_session(session)
        messages.success(request, 'Session cancelled; confirmed students were notified by email where configured.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@login_required
@require_POST
def session_book(request, session_id):
    _student(request)
    s = get_object_or_404(
        TeachingSession, pk=session_id,
        teacher__teacher_profile__verification_status='approved',
    )
    try:
        if not services.is_session_class_student(request.user, s):
            return redirect('market:session_checkout', session_id=s.pk)
        b = services.book_session(request.user, s)
        b.session = s
        b.student = request.user
        services.notify_booking(b)
        messages.success(request, 'Booked.' if b.status == SessionBooking.Status.CONFIRMED else
                         'Booking held. It is confirmed once your payment is verified by an administrator.')
        if b.status == SessionBooking.Status.CONFIRMED and services.is_session_class_student(request.user, s):
            return redirect('market:session_classroom', session_id=s.pk)
        return redirect('market:session_detail', session_id=s.pk)
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=s.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def session_checkout(request, session_id):
    _student(request)
    session = get_object_or_404(
        TeachingSession, pk=session_id,
        teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED,
    )
    if services.is_session_class_student(request.user, session):
        messages.info(request, 'Enrolled students can join this class for free.')
        return redirect('market:session_detail', session_id=session.pk)
    if session.access_type not in (TeachingSession.AccessType.PUBLIC_ONLY,
                                   TeachingSession.AccessType.CLASS_PUBLIC):
        raise Http404
    methods = TeacherPaymentMethod.objects.filter(teacher=session.teacher, is_active=True)
    if request.method == 'POST':
        method = methods.filter(pk=request.POST.get('payment_method')).first()
        try:
            payment = services.submit_teacher_payment(
                request.user,
                session=session,
                payment_method=method,
                transaction_reference=request.POST.get('transaction_reference', ''),
                receipt=request.FILES.get('receipt'),
            )
            messages.success(request, 'Payment submitted. Your booking remains pending until the teacher verifies it.')
            return redirect('market:payment_result', payment_reference=payment.reference)
        except MarketError as exc:
            messages.error(request, str(exc))
    return render(request, 'marketplace/session_checkout.html', {
        'session': session,
        'teacher_payment_methods': methods,
    })

@login_required
@require_POST
def bank_transfer_submit(request, payment_id):
    _student(request)
    payment = get_object_or_404(Payment, pk=payment_id, student=request.user,
                                method=Payment.Method.BANK_TRANSFER)
    receipt = request.FILES.get('receipt')
    if receipt:
        header = receipt.read(8)
        receipt.seek(0)
        valid_signature = (header.startswith(b'%PDF-') or header.startswith(b'\x89PNG\r\n\x1a\n')
                           or header.startswith(b'\xff\xd8\xff'))
        if receipt.size > 5 * 1024 * 1024 or not valid_signature:
            messages.error(request, 'Upload a valid JPG, PNG, or PDF receipt up to 5 MB.')
            return redirect('market:session_checkout', session_id=payment.session_id)
    try:
        services.submit_bank_transfer(payment, request.user, request.POST.get('transaction_reference', ''), receipt)
        messages.success(request, 'Transfer details submitted. Your booking will be confirmed after admin verification.')
        return redirect('market:payment_history')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_checkout', session_id=payment.session_id)


@login_required
def payment_result(request, payment_reference):
    _student(request)
    payment = get_object_or_404(Payment, reference=payment_reference, student=request.user)
    return render(request, 'marketplace/payment_result.html', {'payment': payment})


@login_required
def payment_history(request):
    if request.user.is_student:
        payments = Payment.objects.filter(student=request.user)
    elif request.user.is_teacher:
        payments = Payment.objects.filter(teacher=request.user)
    else:
        raise PermissionDenied
    payments = payments.select_related('student', 'teacher', 'session', 'resource', 'classroom').order_by('-created_at')
    return render(request, 'marketplace/payment_history.html', {'payments': payments,
        'teacher_earnings': payments.filter(status=Payment.Status.CONFIRMED).aggregate(total=Sum('teacher_earning'))['total'] or 0})


@login_required
def payment_receipt(request, payment_id):
    payment = get_object_or_404(Payment, pk=payment_id)
    allowed = request.user.is_admin_role or (
        payment.is_manual_teacher_payment and request.user.pk in (payment.student_id, payment.teacher_id)
    ) or (not payment.is_manual_teacher_payment and request.user.is_admin_role)
    if not allowed:
        raise PermissionDenied
    if not payment.receipt:
        raise Http404
    receipt_extension = os.path.splitext(payment.receipt.name)[1].lower()
    response = FileResponse(payment.receipt.open('rb'), as_attachment=True,
                            filename=f'{payment.reference}-receipt{receipt_extension}')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@csrf_exempt
@require_POST
def stripe_webhook(request):
    from .payment_gateways.stripe_gateway import parse_verified_event
    try:
        event = parse_verified_event(request)
    except Exception:
        return HttpResponse(status=400)
    event_type = event['type']
    if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        checkout = event['data']['object']
        if checkout.get('payment_status') == 'paid':
            reference = (checkout.get('metadata') or {}).get('payment_reference')
            services.confirm_provider_payment(
                reference, checkout.get('id', ''), checkout.get('amount_total'),
                checkout.get('currency', ''), checkout.get('payment_intent') or checkout.get('id', ''),
            )
    elif event_type in ('checkout.session.async_payment_failed', 'checkout.session.expired'):
        checkout = event['data']['object']
        reference = (checkout.get('metadata') or {}).get('payment_reference')
        services.reject_provider_payment(reference, checkout.get('id', ''))
    return HttpResponse(status=200)


@login_required
@require_POST
def session_booking_cancel(request, session_id):
    _student(request)
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        services.cancel_session_booking(request.user, session)
        services.notify_booking_cancelled(request.user, session)
        messages.success(request, 'Your booking was cancelled.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@login_required
def session_join(request, session_id):
    _student(request)
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        _classroom_role(request, session)
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)
    return redirect('market:session_classroom', session_id=session.pk)


# ---------------- reviews ----------------

@login_required
@require_POST
def review_create(request, teacher_id):
    _student(request)
    t = _approved_teacher_or_404(teacher_id)
    if not services.can_review(request.user, t.user):
        messages.error(request, 'You can review a teacher after joining their class or attending a session.')
        return redirect('teachers:public_profile', teacher_id=t.id)
    form = ReviewForm(request.POST)
    if form.is_valid():
        Review.objects.update_or_create(teacher=t.user, student=request.user, defaults={
            'rating': form.cleaned_data['rating'], 'comment': form.cleaned_data['comment'],
            'status': Review.Status.PENDING})
        messages.success(request, 'Thanks! Your review will appear after moderation.')
    return redirect('teachers:public_profile', teacher_id=t.id)


# ---------------- admin: payments + review moderation ----------------

@admin_required
def admin_queue(request):
    return render(request, 'marketplace/admin_queue.html', {
        'payments': Payment.objects.filter(status=Payment.Status.PENDING, is_manual_teacher_payment=False)
            .select_related('student', 'teacher', 'resource', 'session', 'classroom'),
        'reviews': Review.objects.filter(status=Review.Status.PENDING).select_related('student', 'teacher')})


@admin_required
@require_POST
def admin_payment(request, payment_id):
    try:
        services.decide_payment(request.user, payment_id, request.POST.get('decision') == 'confirm')
        messages.success(request, 'Payment updated.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:admin_queue')


@admin_required
@require_POST
def admin_review(request, review_id):
    r = get_object_or_404(Review, pk=review_id)
    r.status = Review.Status.APPROVED if request.POST.get('decision') == 'approve' else Review.Status.REJECTED
    r.save(update_fields=['status'])
    return redirect('market:admin_queue')
