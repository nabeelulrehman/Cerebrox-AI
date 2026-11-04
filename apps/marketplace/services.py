import os
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.classrooms.models import Enrollment
from .models import Resource, Payment, TeachingSession, SessionBooking, Review

ALLOWED_EXT = {'.pdf', '.ppt', '.pptx', '.doc', '.docx', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.mp4', '.webm', '.zip'}
# html/svg/js are deliberately excluded: they could run script if ever rendered inline.


class MarketError(Exception):
    pass


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
    if session.starts_at <= timezone.now():
        raise MarketError('This session has already started.')
    live = session.bookings.exclude(status=SessionBooking.Status.CANCELLED)
    if live.filter(student=student).exists():
        raise MarketError('You already booked this session.')
    if live.count() >= session.capacity:
        raise MarketError('This session is full.')
    if session.price > 0:
        booking = SessionBooking.objects.create(session=session, student=student,
                                                status=SessionBooking.Status.PENDING_PAYMENT)
        Payment.objects.create(student=student, teacher=session.teacher, session=session, amount=session.price)
    else:
        booking = SessionBooking.objects.create(session=session, student=student,
                                                status=SessionBooking.Status.CONFIRMED)
    return booking


def can_review(student, teacher) -> bool:
    return student.is_student and (
        Enrollment.objects.filter(student=student, classroom__teacher=teacher, status=Enrollment.Status.ACTIVE).exists()
        or SessionBooking.objects.filter(student=student, session__teacher=teacher,
                                         status=SessionBooking.Status.CONFIRMED).exists())
