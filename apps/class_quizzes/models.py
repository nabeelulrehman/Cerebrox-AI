"""
Teacher-managed quizzes. COMPLETELY SEPARATE from apps.quizzes.Quiz (independent practice):
different tables, different scoring, different reports. Independent analytics query only
apps.quizzes.Quiz, so class scores can never leak into personal analytics and vice versa.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class ClassQuiz(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        CLOSED = 'closed', 'Closed'

    class Visibility(models.TextChoices):
        IMMEDIATE = 'immediate', 'Show result right after grading is complete'
        AFTER_END = 'after_end', 'Show after the quiz window closes'
        MANUAL = 'manual', 'Only when I release results'

    classroom = models.ForeignKey('classrooms.Classroom', related_name='class_quizzes', on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='+', on_delete=models.CASCADE)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    # unguessable id used in shareable links (WhatsApp/email). The link alone grants NOTHING:
    # login + enrollment + schedule + attempts are all re-checked server-side.
    share_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=30)
    max_attempts = models.PositiveIntegerField(default=1)
    shuffle_questions = models.BooleanField(default=False)
    negative_marking = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('0'),
        help_text='Fraction of a question\'s marks deducted for a wrong MCQ answer (0 = off, 0.25 = quarter).')
    supervised = models.BooleanField(default=False, help_text='Require teacher QR verification before starting.')
    result_visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.IMMEDIATE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'class quiz'
        constraints = [
            models.CheckConstraint(check=Q(max_attempts__gte=1), name='classquiz_attempts_gte_1'),
            models.CheckConstraint(check=Q(duration_minutes__gte=1), name='classquiz_duration_gte_1'),
            models.CheckConstraint(
                check=Q(end_at__isnull=True) | Q(start_at__isnull=True) | Q(end_at__gt=models.F('start_at')),
                name='classquiz_end_after_start'),
        ]

    def __str__(self):
        return self.title

    @property
    def total_marks(self):
        return self.questions.aggregate(t=models.Sum('marks'))['t'] or Decimal('0')


class ClassQuestion(models.Model):
    class Kind(models.TextChoices):
        MCQ = 'mcq', 'Multiple choice'
        DESCRIPTIVE = 'descriptive', 'Descriptive'

    quiz = models.ForeignKey(ClassQuiz, related_name='questions', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.MCQ)
    text = models.TextField()
    option_a = models.CharField(max_length=255, blank=True)
    option_b = models.CharField(max_length=255, blank=True)
    option_c = models.CharField(max_length=255, blank=True)
    option_d = models.CharField(max_length=255, blank=True)
    correct_answer = models.CharField(max_length=1, blank=True)
    model_answer = models.TextField(blank=True, help_text='Descriptive: model answer / rubric (teacher-only).')
    marks = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1'))
    explanation = models.TextField(blank=True)
    ai_generated = models.BooleanField(default=False)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.CheckConstraint(check=Q(marks__gt=0), name='classquestion_marks_positive')]

    def options(self):
        return [('A', self.option_a), ('B', self.option_b), ('C', self.option_c), ('D', self.option_d)]


class QuizAttempt(models.Model):
    class Status(models.TextChoices):
        AWAITING = 'awaiting', 'Awaiting teacher verification'
        IN_PROGRESS = 'in_progress', 'In progress'
        PAUSED = 'paused', 'Paused - re-verification needed'
        SUBMITTED = 'submitted', 'Submitted'

    quiz = models.ForeignKey(ClassQuiz, related_name='attempts', on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='class_attempts', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)   # server-controlled, never extended
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_late = models.BooleanField(default=False)
    pause_count = models.PositiveIntegerField(default=0)
    question_order = models.JSONField(default=list, blank=True)
    # grading
    auto_score = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    final_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    total_marks = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    grading_complete = models.BooleanField(default=False)
    results_released = models.BooleanField(default=False)
    teacher_feedback = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['quiz', 'student', 'number'], name='uniq_attempt_number'),
            # at most ONE unfinished attempt per student per quiz, enforced by the database
            models.UniqueConstraint(
                fields=['quiz', 'student'], condition=~Q(status='submitted'), name='one_open_attempt_per_student'),
        ]
        indexes = [models.Index(fields=['quiz', 'status'])]

    @property
    def percentage(self):
        if self.final_score is None or not self.total_marks:
            return None
        return round(float(self.final_score) / float(self.total_marks) * 100, 1)


class AttemptAnswer(models.Model):
    attempt = models.ForeignKey(QuizAttempt, related_name='answers', on_delete=models.CASCADE)
    question = models.ForeignKey(ClassQuestion, related_name='+', on_delete=models.CASCADE)
    selected = models.CharField(max_length=1, blank=True)
    text_answer = models.TextField(blank=True)
    auto_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    awarded_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ai_suggested_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ai_feedback = models.TextField(blank=True)
    teacher_feedback = models.TextField(blank=True)
    reviewed = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['attempt', 'question'], name='uniq_attempt_question')]


class VerificationToken(models.Model):
    """Single-use, short-lived proof that THIS attempt should be authorized by a teacher.
    Only the SHA-256 hash is stored; the raw token exists only inside the student's QR."""
    attempt = models.ForeignKey(QuizAttempt, related_name='tokens', on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+',
                                on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    quiz = models.ForeignKey(ClassQuiz, null=True, blank=True, related_name='audit', on_delete=models.CASCADE)
    attempt = models.ForeignKey(QuizAttempt, null=True, blank=True, related_name='audit', on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, related_name='+',
                              on_delete=models.SET_NULL)
    action = models.CharField(max_length=40)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
