from django.contrib import admin
from .models import ClassQuiz, ClassQuestion, QuizAttempt, AuditLog

admin.site.register(ClassQuiz)
admin.site.register(ClassQuestion)
admin.site.register(QuizAttempt)
admin.site.register(AuditLog)
