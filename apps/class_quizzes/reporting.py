"""Class-quiz reports. Only ever reads class_quizzes tables, so independent quiz
scores (apps.quizzes.Quiz) can never appear here."""
from django.db.models import Avg, Max, Min, Count
from .models import QuizAttempt


def quiz_report(quiz):
    attempts = (QuizAttempt.objects.filter(quiz=quiz, status=QuizAttempt.Status.SUBMITTED)
                .select_related('student').order_by('student__first_name', 'student__username', 'number'))
    graded = [a for a in attempts if a.grading_complete]
    pcts = [a.percentage for a in graded if a.percentage is not None]
    return {
        'attempts': attempts,
        'submitted': len(attempts),
        'graded': len(graded),
        'pending_review': len(attempts) - len(graded),
        'average': round(sum(pcts) / len(pcts), 1) if pcts else None,
        'highest': max(pcts) if pcts else None,
        'lowest': min(pcts) if pcts else None,
    }


def student_class_summary(classroom, student):
    attempts = (QuizAttempt.objects.filter(quiz__classroom=classroom, student=student,
                                           status=QuizAttempt.Status.SUBMITTED)
                .select_related('quiz'))
    pcts = [a.percentage for a in attempts if a.percentage is not None]
    return {'attempts': attempts, 'average': round(sum(pcts) / len(pcts), 1) if pcts else None}
