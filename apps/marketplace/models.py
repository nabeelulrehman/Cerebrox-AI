"""Optional teacher services: materials, paid access, guidance, sessions, reviews.
Nothing in the independent-learning apps depends on this module."""
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import Q

# Files live OUTSIDE MEDIA_ROOT/MEDIA_URL: they are only reachable through the
# permission-checked download view, never via a guessable URL.
private_storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


def _upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f'{uuid.uuid4().hex}{ext}'


def _payment_ref():
    return uuid.uuid4().hex[:12].upper()


class Resource(models.Model):
    class Visibility(models.TextChoices):
        DRAFT = 'draft', 'Private draft'
        CLASSES = 'classes', 'Selected classes only'
        PUBLIC = 'public', 'Public educational resource'
        PAID = 'paid', 'Paid (verified purchase required)'

    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='resources', on_delete=models.CASCADE)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    file = models.FileField(storage=private_storage, upload_to=_upload_to)
    original_name = models.CharField(max_length=255, blank=True)
    visibility = models.CharField(max_length=8, choices=Visibility.choices, default=Visibility.DRAFT, db_index=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    classrooms = models.ManyToManyField('classrooms.Classroom', related_name='resources', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(
            check=~Q(visibility='paid') | Q(price__gt=0), name='paid_resource_needs_price')]

    def __str__(self):
        return self.title


class Payment(models.Model):
    """A purchase CLAIM. Access is granted only once an administrator confirms the payment
    (no payment gateway is integrated; confirmation is a manual, audited step)."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Awaiting confirmation'
        CONFIRMED = 'confirmed', 'Confirmed'
        REJECTED = 'rejected', 'Rejected'

    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='payments', on_delete=models.CASCADE)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, null=True, blank=True, related_name='payments', on_delete=models.CASCADE)
    session = models.ForeignKey('TeachingSession', null=True, blank=True, related_name='payments',
                                on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    reference = models.CharField(max_length=40, unique=True, default=_payment_ref)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+',
                                     on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(check=Q(resource__isnull=False) | Q(session__isnull=False),
                                   name='payment_has_target'),
            models.UniqueConstraint(fields=['student', 'resource'], condition=Q(resource__isnull=False)
                                    & ~Q(status='rejected'), name='one_live_payment_per_resource'),
            models.UniqueConstraint(fields=['student', 'session'], condition=Q(session__isnull=False)
                                    & ~Q(status='rejected'), name='one_live_payment_per_session'),
        ]


class GuidanceRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        ANSWERED = 'answered', 'Answered'
        CLOSED = 'closed', 'Closed'

    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='guidance_requests', on_delete=models.CASCADE)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='guidance_inbox', on_delete=models.CASCADE)
    subject = models.CharField(max_length=160, blank=True, default='Guidance request')
    message = models.TextField(max_length=2000)
    reply = models.TextField(blank=True, max_length=2000)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='started_guidance_requests',
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class GuidanceMessage(models.Model):
    conversation = models.ForeignKey(GuidanceRequest, related_name='messages', on_delete=models.CASCADE)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='guidance_messages', on_delete=models.CASCADE)
    body = models.TextField(max_length=2000)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sent_at', 'pk']


class TeachingSession(models.Model):
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='teaching_sessions', on_delete=models.CASCADE)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    capacity = models.PositiveIntegerField(default=1)
    meeting_link = models.URLField(blank=True, help_text='Shown only to confirmed attendees.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['starts_at']
        constraints = [models.CheckConstraint(check=Q(capacity__gte=1), name='session_capacity_gte_1')]


class SessionBooking(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', 'Awaiting payment confirmation'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    session = models.ForeignKey(TeachingSession, related_name='bookings', on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='session_bookings', on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['session', 'student'], name='uniq_booking')]


class Review(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending moderation'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='reviews_received', on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='reviews_written', on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(max_length=1000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['teacher', 'student'], name='one_review_per_pair'),
            models.CheckConstraint(check=Q(rating__gte=1) & Q(rating__lte=5), name='rating_1_to_5'),
        ]
