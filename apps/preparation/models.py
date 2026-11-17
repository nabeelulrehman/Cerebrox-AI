from django.conf import settings
from django.db import models


class PreparationPlan(models.Model):
    class Level(models.TextChoices):
        BEGINNER = 'beginner', 'Beginner'
        INTERMEDIATE = 'intermediate', 'Intermediate'
        ADVANCED = 'advanced', 'Advanced'

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preparation_plans')
    exam_name = models.CharField(max_length=160)
    exam_type = models.CharField(max_length=80, blank=True)
    deadline = models.DateField()
    syllabus_text = models.TextField()
    learning_level = models.CharField(max_length=16, choices=Level.choices, default=Level.INTERMEDIATE)
    goals = models.TextField(blank=True)
    study_hours_per_week = models.PositiveSmallIntegerField(default=7)
    study_days = models.JSONField(default=list)
    preferred_time = models.TimeField(null=True, blank=True)
    session_minutes = models.PositiveSmallIntegerField(default=60)
    timezone = models.CharField(max_length=64, default='Asia/Karachi')
    email_reminders = models.BooleanField(default=False)
    active = models.BooleanField(default=True, db_index=True)
    reschedule_notice = models.TextField(blank=True)
    next_recommendations = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['deadline', '-created_at']
        indexes = [models.Index(fields=['student', 'active', 'deadline'])]

    def __str__(self):
        return f'{self.exam_name} preparation for {self.student}'


class PreparationTopic(models.Model):
    plan = models.ForeignKey(PreparationPlan, on_delete=models.CASCADE, related_name='topics')
    learning_topic = models.ForeignKey('learning.Topic', null=True, blank=True, on_delete=models.SET_NULL, related_name='preparation_topics')
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    priority = models.PositiveSmallIntegerField(default=3)
    order = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['plan', 'name'], name='unique_preparation_topic')]


class LearningSession(models.Model):
    class Kind(models.TextChoices):
        STUDY = 'study', 'Study'
        QUIZ = 'quiz', 'Practice quiz'
        REVISION = 'revision', 'Revision'
        MOCK = 'mock', 'Mock examination'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Upcoming'
        COMPLETED = 'completed', 'Completed'
        MISSED = 'missed', 'Missed'

    plan = models.ForeignKey(PreparationPlan, on_delete=models.CASCADE, related_name='sessions')
    topic = models.ForeignKey(PreparationTopic, null=True, blank=True, on_delete=models.SET_NULL, related_name='sessions')
    title = models.CharField(max_length=180)
    objectives = models.TextField(blank=True)
    material = models.TextField(blank=True)
    scheduled_at = models.DateTimeField(db_index=True)
    scheduled_end_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.STUDY)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['scheduled_at', 'id']
        indexes = [models.Index(fields=['plan', 'status', 'scheduled_at'])]


class PreparationAssessment(models.Model):
    plan = models.ForeignKey(PreparationPlan, on_delete=models.CASCADE, related_name='assessments')
    topic = models.ForeignKey(PreparationTopic, null=True, blank=True, on_delete=models.SET_NULL, related_name='assessments')
    kind = models.CharField(max_length=12, choices=LearningSession.Kind.choices)
    score = models.DecimalField(max_digits=5, decimal_places=2)
    possible = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class PreparationNotification(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='preparation_notifications')
    plan = models.ForeignKey(PreparationPlan, on_delete=models.CASCADE, related_name='notifications')
    session = models.ForeignKey(LearningSession, null=True, blank=True, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=24)
    dedupe_key = models.CharField(max_length=180, unique=True)
    title = models.CharField(max_length=180)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['student', 'read_at', '-created_at'])]


class ReadinessSnapshot(models.Model):
    plan = models.ForeignKey(PreparationPlan, on_delete=models.CASCADE, related_name='readiness_history')
    score = models.PositiveSmallIntegerField()
    confidence = models.CharField(max_length=16)
    explanation = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
