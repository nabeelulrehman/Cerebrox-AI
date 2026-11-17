from django.db import models
from django.conf import settings
from apps.learning.models import Topic


class Question(models.Model):
    ANSWER_CHOICES = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')]
    DIFFICULTY_CHOICES = [('Easy', 'Easy'), ('Medium', 'Medium'), ('Hard', 'Hard')]

    topic = models.ForeignKey(Topic, related_name='questions', on_delete=models.CASCADE)
    question = models.TextField()
    option_a = models.CharField(max_length=255)
    option_b = models.CharField(max_length=255)
    option_c = models.CharField(max_length=255)
    option_d = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=1, choices=ANSWER_CHOICES)
    explanation = models.TextField(blank=True)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default='Medium')
    ai_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def options(self):
        return [('A', self.option_a), ('B', self.option_b), ('C', self.option_c), ('D', self.option_d)]

    def __str__(self):
        return self.question[:60]


class Quiz(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='quizzes', on_delete=models.CASCADE)
    topic = models.ForeignKey(Topic, related_name='quizzes', on_delete=models.CASCADE)
    preparation_plan = models.ForeignKey(
        'preparation.PreparationPlan', null=True, blank=True,
        related_name='quiz_attempts', on_delete=models.SET_NULL,
    )
    preparation_topic = models.ForeignKey(
        'preparation.PreparationTopic', null=True, blank=True,
        related_name='quiz_attempts', on_delete=models.SET_NULL,
    )
    preparation_session = models.ForeignKey(
        'preparation.LearningSession', null=True, blank=True,
        related_name='quiz_attempts', on_delete=models.SET_NULL,
    )
    topics = models.ManyToManyField(Topic, related_name='quiz_attempts', blank=True)
    difficulty = models.CharField(max_length=10, default='Medium')
    study_level = models.CharField(max_length=60, blank=True, help_text='Snapshot of student.effective_study_level')
    total_questions = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    percentage = models.FloatField(default=0)
    duration_seconds = models.PositiveIntegerField(default=600)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.student} - {self.topic} - {self.percentage:.0f}%'

    @property
    def wrong_count(self):
        return max(self.total_questions - self.score, 0)

    @property
    def performance_label(self):
        if self.percentage >= 70:
            return 'Good'
        if self.percentage >= 50:
            return 'Needs Practice'
        return 'Weak'


class QuizAnswer(models.Model):
    quiz = models.ForeignKey(Quiz, related_name='answers', on_delete=models.CASCADE)
    question = models.ForeignKey(Question, related_name='+', on_delete=models.CASCADE)
    selected_answer = models.CharField(max_length=1, blank=True)
    correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ('quiz', 'question')

    @property
    def correct_answer_text(self):
        return dict(self.question.options()).get(self.question.correct_answer, '')

    @property
    def selected_answer_text(self):
        return dict(self.question.options()).get((self.selected_answer or '').upper(), '')
