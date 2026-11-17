from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.ai_engine.gemini_client import AIUnavailableError
from apps.learning.models import AINote
from apps.quizzes.models import Quiz
from apps.quizzes.services import QuizGenerationInProgress, create_ai_quiz
from .forms import PreparationPlanForm, AssessmentForm
from .models import PreparationPlan, LearningSession, PreparationAssessment, PreparationNotification
from .services import extract_syllabus, generate_plan, readiness, reschedule, repair_collapsed_schedule, record_readiness, update_recommendations, _plan_timezone


def _student(request):
    if not request.user.is_student:
        raise PermissionDenied


@login_required
def dashboard(request):
    _student(request)
    plans = PreparationPlan.objects.filter(student=request.user, active=True).prefetch_related('topics', 'sessions')
    cards = []
    for plan in plans:
        next_session = plan.sessions.filter(status='pending', scheduled_at__gte=timezone.now()).first()
        next_display = ''
        if next_session:
            next_start = timezone.localtime(next_session.scheduled_at, _plan_timezone(plan))
            next_display = f'{next_start:%b} {next_start.day}, {next_start:%I:%M %p}'.replace(' 0', ' ')
        cards.append({'plan': plan, 'readiness': readiness(plan),
            'completed': plan.sessions.filter(status='completed').count(), 'total': plan.sessions.count(),
            'next': next_session, 'next_display': next_display})
    return render(request, 'preparation/dashboard.html', {'cards': cards,
        'notifications': PreparationNotification.objects.filter(student=request.user)[:10]})


@login_required
@require_POST
def notification_read(request, notification_id):
    _student(request)
    notification = get_object_or_404(PreparationNotification, pk=notification_id, student=request.user)
    notification.read_at = timezone.now()
    notification.save(update_fields=['read_at'])
    return redirect('preparation:dashboard')


@login_required
def plan_create(request):
    _student(request)
    form = PreparationPlanForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        syllabus = data['syllabus_text'].strip()
        if data.get('syllabus_file'):
            try:
                extracted = extract_syllabus(data['syllabus_file'])
            except (ValueError, ImportError, Exception) as exc:
                form.add_error('syllabus_file', str(exc) or 'Could not read this syllabus file.')
                return render(request, 'preparation/plan_form.html', {'form': form})
            syllabus = '\n'.join(part for part in (syllabus, extracted) if part).strip()
        plan = form.save(commit=False)
        plan.student = request.user
        plan.syllabus_text = syllabus[:30000]
        plan.study_days = data.get('study_days', [])
        plan.save()
        try:
            with transaction.atomic():
                topic_count, session_count = generate_plan(plan)
        except (AIUnavailableError, ValueError) as exc:
            plan.delete()
            messages.error(request, f'Could not generate the plan: {exc}')
            return render(request, 'preparation/plan_form.html', {'form': form})
        messages.success(request, f'Your plan includes {topic_count} topics and {session_count} study activities.')
        return redirect('preparation:detail', plan_id=plan.pk)
    return render(request, 'preparation/plan_form.html', {'form': form})


@login_required
def plan_detail(request, plan_id):
    _student(request)
    plan = get_object_or_404(PreparationPlan, pk=plan_id, student=request.user)
    # Repair legacy plans created while the schedule generator collapsed all
    # pending work onto one date. Completed sessions are left untouched.
    repair_collapsed_schedule(plan)
    assessment_form = AssessmentForm(plan=plan)
    availability_form = PreparationPlanForm(instance=plan)
    sessions = plan.sessions.select_related('topic', 'topic__learning_topic').prefetch_related('topic__learning_topic__ai_notes').all()
    plan_tz = _plan_timezone(plan)
    for session in sessions:
        learning_topic = session.topic.learning_topic if session.topic else None
        session.latest_note = AINote.objects.filter(student=request.user, topic=learning_topic).first() if learning_topic else None
        session.start_local = timezone.localtime(session.scheduled_at, plan_tz)
        session.end_local = timezone.localtime(session.scheduled_end_at, plan_tz) if session.scheduled_end_at else session.start_local + timedelta(minutes=session.duration_minutes)
        # Keep the exact plan-local wall time: Django's template date filter
        # localizes aware datetimes again using the active (global) timezone.
        session.start_display = _format_plan_datetime(session.start_local, include_date=True)
        session.end_display = _format_plan_datetime(session.end_local, include_date=False)
    recent_quizzes = Quiz.objects.filter(student=request.user).filter(
        Q(preparation_plan=plan) | Q(topic__preparation_topics__plan=plan), completed=True,
    ).distinct().order_by('-created_at')[:5]
    return render(request, 'preparation/plan_detail.html', {
        'plan': plan, 'readiness': readiness(plan), 'assessment_form': assessment_form,
        'availability_form': availability_form,
        'sessions': sessions, 'now': timezone.now(),
        'recent_quizzes': recent_quizzes,
        'completed_topic_count': plan.topics.filter(completed_at__isnull=False).count(),
        'readiness_history': plan.readiness_history.all()[:12],
    })


def _format_plan_datetime(value, *, include_date):
    clock = value.strftime('%I:%M %p').lstrip('0')
    if include_date:
        return f'{value:%a}, {value:%b} {value.day} · {clock}'
    return clock


@login_required
@require_POST
def update_availability(request, plan_id):
    _student(request)
    plan = get_object_or_404(PreparationPlan, pk=plan_id, student=request.user)
    form = PreparationPlanForm(request.POST, request.FILES or None, instance=plan)
    if form.is_valid():
        plan = form.save(commit=False)
        plan.study_days = form.cleaned_data.get('study_days', [])
        plan.save()
        messages.success(request, 'Availability updated. Review and apply a reschedule to move existing sessions.')
    else:
        messages.error(request, 'Please correct the availability form.')
    return redirect('preparation:detail', plan_id=plan.pk)


@login_required
def reschedule_preview(request, plan_id):
    _student(request)
    plan = get_object_or_404(PreparationPlan, pk=plan_id, student=request.user)
    pending = plan.sessions.filter(status__in=['pending', 'missed']).order_by('scheduled_at')
    return render(request, 'preparation/reschedule.html', {'plan': plan, 'sessions': pending})


@login_required
@require_POST
def reschedule_apply(request, plan_id):
    _student(request)
    plan = get_object_or_404(PreparationPlan, pk=plan_id, student=request.user)
    try:
        moved, total = reschedule(plan)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('preparation:detail', plan_id=plan.pk)
    messages.info(request, f'Revised schedule: moved {moved} of {total} unfinished activities. {plan.reschedule_notice}')
    return redirect('preparation:detail', plan_id=plan.pk)


@login_required
@require_POST
def complete_session(request, plan_id, session_id):
    _student(request)
    session = get_object_or_404(LearningSession, pk=session_id, plan__pk=plan_id, plan__student=request.user)
    if session.status != LearningSession.Status.COMPLETED:
        session.status = LearningSession.Status.COMPLETED
        session.completed_at = timezone.now()
        session.save(update_fields=['status', 'completed_at'])
        record_readiness(session.plan, 'A learning activity was completed.')
        if session.topic and session.topic.completed_at is None:
            session.topic.completed_at = session.completed_at
            session.topic.save(update_fields=['completed_at'])
        messages.success(request, 'Study session marked complete. Add an assessment score to track understanding.')
    return redirect('preparation:detail', plan_id=plan_id)


@login_required
@require_POST
def practice_quiz(request, plan_id, session_id):
    _student(request)
    session = get_object_or_404(
        LearningSession.objects.select_related('topic__learning_topic', 'plan'),
        pk=session_id, plan__pk=plan_id, plan__student=request.user,
    )
    if not session.topic or not session.topic.learning_topic:
        messages.error(request, 'This activity is not linked to a learning topic yet.')
        return redirect('preparation:detail', plan_id=plan_id)
    if not session.topic.completed_at:
        messages.info(request, 'Mark this topic as learned before starting its practice quiz.')
        return redirect('preparation:detail', plan_id=plan_id)
    try:
        quiz = create_ai_quiz(
            request.user, session.topic.learning_topic, 'Medium', 5,
            preparation_plan=session.plan, preparation_topic=session.topic,
            preparation_session=session,
        )
    except QuizGenerationInProgress:
        messages.info(request, 'This topic quiz is already being generated. Please wait a moment.')
        return redirect('preparation:detail', plan_id=plan_id)
    except AIUnavailableError:
        messages.error(request, 'Could not generate the practice quiz right now. Please try again shortly.')
        return redirect('preparation:detail', plan_id=plan_id)
    if getattr(quiz, 'used_fallback', False):
        messages.warning(request, 'AI questions were unavailable, so this quiz uses the existing fallback question set.')
    return redirect('quizzes:take', quiz_id=quiz.pk)


@login_required
@require_POST
def assessment_add(request, plan_id):
    _student(request)
    plan = get_object_or_404(PreparationPlan, pk=plan_id, student=request.user)
    form = AssessmentForm(request.POST, plan=plan)
    if form.is_valid():
        PreparationAssessment.objects.create(plan=plan, **form.cleaned_data)
        record_readiness(plan, 'New quiz or mock result recorded.')
        update_recommendations(plan)
        messages.success(request, 'Assessment recorded. Your readiness estimate now reflects this result.')
    else:
        messages.error(request, 'Enter a valid assessment result.')
    return redirect('preparation:detail', plan_id=plan.pk)
