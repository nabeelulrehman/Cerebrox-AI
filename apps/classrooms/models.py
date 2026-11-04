"""
Teacher-owned classes and (always optional) student enrollment.

Ownership rules:
- A Classroom belongs to exactly one teacher.
- Enrollment never changes ownership of a student's personal courses/notes/quizzes;
  those live in apps.learning / apps.quizzes and have no link to this app.
"""
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q


def _new_join_code():
    return secrets.token_urlsafe(6).upper().replace('-', 'X').replace('_', 'Y')[:8]


class Classroom(models.Model):
    class Policy(models.TextChoices):
        INVITE_ONLY = 'invite_only', 'Invite only'
        APPROVAL = 'approval', 'Join code + teacher approval'
        OPEN_CODE = 'open_code', 'Anyone with the join code'

    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='taught_classes', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    join_code = models.CharField(max_length=12, unique=True, default=_new_join_code, editable=False)
    enrollment_policy = models.CharField(max_length=12, choices=Policy.choices, default=Policy.APPROVAL)
    listed_publicly = models.BooleanField(default=False, help_text='Show on your public teacher profile.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['teacher', 'name'], name='uniq_class_name_per_teacher')]

    def __str__(self):
        return f'{self.name} ({self.teacher})'

    def regenerate_code(self):
        self.join_code = _new_join_code()
        self.save(update_fields=['join_code'])

    def active_students(self):
        return self.enrollments.filter(status=Enrollment.Status.ACTIVE).select_related('student')


class Enrollment(models.Model):
    class Status(models.TextChoices):
        INVITED = 'invited', 'Invited'
        REQUESTED = 'requested', 'Requested'
        ACTIVE = 'active', 'Active'
        DECLINED = 'declined', 'Declined'
        REMOVED = 'removed', 'Removed'

    classroom = models.ForeignKey(Classroom, related_name='enrollments', on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='enrollments', on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    # Explicit, revocable consent to show the teacher a summary of INDEPENDENT progress.
    share_progress = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['classroom', 'student'], name='uniq_enrollment')]
        indexes = [models.Index(fields=['student', 'status'])]

    def __str__(self):
        return f'{self.student} in {self.classroom} [{self.status}]'


class Announcement(models.Model):
    classroom = models.ForeignKey(Classroom, related_name='announcements', on_delete=models.CASCADE)
    title = models.CharField(max_length=140)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
