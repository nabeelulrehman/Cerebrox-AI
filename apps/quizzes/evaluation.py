"""Pure backend scoring logic — no AI involved (per FYP scope §18)."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone


@transaction.atomic
def submit_quiz(quiz, answers_by_question_id: dict):
    """answers_by_question_id: {question_id(str/int): 'A'|'B'|'C'|'D'}"""
    correct_count = 0
    quiz_answers = quiz.answers.select_related('question').all()
    for qa in quiz_answers:
        selected = answers_by_question_id.get(str(qa.question_id), '')
        qa.selected_answer = selected
        qa.correct = bool(selected) and selected.upper() == qa.question.correct_answer
        if qa.correct:
            correct_count += 1
        qa.save(update_fields=['selected_answer', 'correct'])

    quiz.score = correct_count
    quiz.percentage = round((correct_count / quiz.total_questions) * 100, 1) if quiz.total_questions else 0
    quiz.completed = True
    quiz.save(update_fields=['score', 'percentage', 'completed'])

    if quiz.preparation_session_id:
        from apps.preparation.models import LearningSession
        session = quiz.preparation_session
        if session.status != LearningSession.Status.COMPLETED:
            if not session.started_at:
                raise ValueError('A preparation task must be started before its quiz can complete it.')
            session.status = LearningSession.Status.COMPLETED
            session.completed_at = timezone.now()
            session.history = [*session.history, {'kind': 'completed', 'at': session.completed_at.isoformat(), 'source': 'quiz'}]
            session.save(update_fields=['status', 'completed_at', 'history'])

    # Feed real completed practice attempts into any matching exam preparation plans.
    from apps.preparation.models import PreparationPlan, PreparationTopic
    from apps.preparation.services import record_readiness
    plan_ids = PreparationTopic.objects.filter(
        Q(learning_topic=quiz.topic) | Q(pk=quiz.preparation_topic_id),
        plan__student=quiz.student, plan__active=True,
    ).values_list('plan_id', flat=True).distinct()
    for plan in PreparationPlan.objects.filter(pk__in=plan_ids):
        record_readiness(plan, f'Quiz result recorded for {quiz.topic.name}.')

    _award_xp(quiz)
    return quiz


def update_preparation_recommendations(quiz):
    """Refresh adaptive guidance after the quiz transaction has committed."""
    from apps.preparation.models import PreparationPlan, PreparationTopic
    from apps.preparation.services import update_recommendations
    plan_ids = PreparationTopic.objects.filter(
        Q(learning_topic=quiz.topic) | Q(pk=quiz.preparation_topic_id),
        plan__student=quiz.student, plan__active=True,
    ).values_list('plan_id', flat=True).distinct()
    if quiz.preparation_plan_id:
        plan_ids = set(plan_ids) | {quiz.preparation_plan_id}
    for plan in PreparationPlan.objects.filter(pk__in=plan_ids):
        update_recommendations(plan)


def _award_xp(quiz):
    from django.conf import settings
    rules = settings.XP_RULES
    xp = rules['QUIZ_COMPLETED']
    if quiz.percentage > 90:
        xp += rules['SCORE_ABOVE_90']
    elif quiz.percentage > 80:
        xp += rules['SCORE_ABOVE_80']
    student = quiz.student
    student.xp_points = (student.xp_points or 0) + xp
    student.save(update_fields=['xp_points'])
