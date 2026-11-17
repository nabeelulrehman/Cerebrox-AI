from django.db import models
from django.conf import settings


class Course(models.Model):
    """
    A student's own, self-defined course (NOT a shared/global subject).
    Each student builds their own learning structure — nothing here is
    predefined or shared across students, per the FYP's custom-course flow.
    """
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='courses', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=8, blank=True, default='📘')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        unique_together = ('student', 'name')

    def __str__(self):
        return f'{self.name} ({self.student})'

    @property
    def topic_count(self):
        return self.topics.count()


class Topic(models.Model):
    """A topic within one of the student's own courses."""
    course = models.ForeignKey(Course, related_name='topics', on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['course__name', 'name']
        unique_together = ('course', 'name')

    def __str__(self):
        return f'{self.course.name} → {self.name}'

    @property
    def student(self):
        return self.course.student


class AINote(models.Model):
    """AI-generated study notes (Module 3 in the FYP scope)."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='ai_notes', on_delete=models.CASCADE)
    topic = models.ForeignKey(Topic, related_name='ai_notes', on_delete=models.CASCADE)
    study_level = models.CharField(max_length=60, help_text="Student's study level snapshot at generation time")
    content = models.JSONField(help_text='Structured notes: introduction, explanation, key_terms, examples, summary')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Notes: {self.topic} ({self.student})'


class AINoteConversation(models.Model):
    """Per-student, per-note tutor context; bounded to keep requests predictable."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='note_conversations')
    note = models.ForeignKey(AINote, on_delete=models.CASCADE, related_name='conversations')
    messages = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['student', 'note'], name='uniq_student_note_conversation')]

    def __str__(self):
        return f'Tutor conversation for note {self.note_id}'
