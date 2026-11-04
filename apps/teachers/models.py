"""
Teacher ecosystem — OPTIONAL add-on. Nothing in apps.learning / apps.quizzes /
apps.analytics imports or depends on this app, so independent AI learning keeps
working with zero teachers registered.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class TeacherProfile(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        SUSPENDED = 'suspended', 'Suspended'

    class Mode(models.TextChoices):
        ONLINE = 'online', 'Online'
        IN_PERSON = 'in_person', 'In person'
        BOTH = 'both', 'Online & in person'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name='teacher_profile', on_delete=models.CASCADE)
    headline = models.CharField(max_length=140, blank=True)
    bio = models.TextField(blank=True)
    qualifications = models.TextField(help_text='Degrees, certifications, institutions.')
    subjects = models.CharField(max_length=255, help_text='Comma-separated, e.g. "Physics, Mathematics"')
    experience_years = models.PositiveIntegerField(default=0)
    teaching_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.ONLINE)
    availability = models.CharField(max_length=200, blank=True, help_text='e.g. "Weekdays after 5pm"')
    offers_free_guidance = models.BooleanField(default=False)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                      help_text='Leave empty if you do not offer paid sessions.')

    verification_status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    verification_note = models.CharField(max_length=255, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    related_name='+', on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['verification_status', 'teaching_mode'])]

    def __str__(self):
        return f'Teacher: {self.user}'

    @property
    def is_approved(self):
        return self.verification_status == self.Status.APPROVED

    @property
    def subject_list(self):
        return [s.strip() for s in self.subjects.split(',') if s.strip()]

    def set_status(self, status, admin_user, note=''):
        self.verification_status = status
        self.verification_note = note[:255]
        self.verified_by = admin_user
        self.verified_at = timezone.now() if status == self.Status.APPROVED else None
        self.save(update_fields=['verification_status', 'verification_note', 'verified_by', 'verified_at'])
