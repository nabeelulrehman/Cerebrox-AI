from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.ai_engine.gemini_client import AIUnavailableError, user_facing_error_message
from apps.learning.models import AINote
from apps.learning.forms import NotesGenerateForm
from apps.ai_engine.notes_generator import NotesGenerationInProgress, generate_notes, notes_generation_lock
from apps.quizzes.models import Quiz
from apps.quizzes.services import QuizGenerationInProgress, create_ai_quiz
from apps.quizzes.evaluation import submit_quiz, update_preparation_recommendations
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
            detail = user_facing_error_message(exc) if isinstance(exc, AIUnavailableError) else str(exc)
            messages.error(request, f'Could not generate the plan: {detail}')
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
    sessions = list(sessions)
    pending_sessions = [session for session in sessions if session.status in (LearningSession.Status.PENDING, LearningSession.Status.MISSED)]
    current_session = pending_sessions[0] if pending_sessions else None
    completed_sessions = [session for session in sessions if session.status == LearningSession.Status.COMPLETED]
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
        'current_session': current_session, 'completed_sessions': completed_sessions,
        'completed_count': len(completed_sessions), 'total_sessions': len(sessions),
        'recent_quizzes': recent_quizzes,
        'completed_topic_count': plan.topics.filter(completed_at__isnull=False).count(),
        'readiness_history': plan.readiness_history.all()[:12],
    })


@login_required
def notes_generate(request, plan_id, session_id):
    _student(request)
    session = get_object_or_404(
        LearningSession.objects.select_related('topic__learning_topic__course', 'plan'),
        pk=session_id, plan__pk=plan_id, plan__student=request.user,
    )
    topic = session.topic.learning_topic if session.topic else None
    if not topic:
        messages.error(request, 'This activity is not linked to a learning topic yet.')
        return redirect('preparation:detail', plan_id=plan_id)
    if not session.started_at:
        messages.error(request, 'Start this task before generating its AI notes.')
        return redirect('preparation:detail', plan_id=plan_id)

    form = NotesGenerateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with notes_generation_lock(request.user.pk, topic.pk, form.cleaned_data['length']):
                content = generate_notes(
                    topic.course.name, topic.name,
                    request.user.effective_study_level, form.cleaned_data['length'],
                )
                note = AINote.objects.create(
                    student=request.user, topic=topic,
                    study_level=request.user.effective_study_level, content=content,
                )
        except NotesGenerationInProgress as exc:
            messages.error(request, str(exc))
        except AIUnavailableError as exc:
            messages.error(request, 'Unable to generate AI notes right now. Please try again shortly.')
            return render(request, 'preparation/notes_generator.html', {
                'plan': session.plan, 'session': session, 'topic': topic,
                'form': form, 'error': user_facing_error_message(exc),
            })
        else:
            messages.success(request, 'AI notes generated and saved to this preparation activity.')
            return redirect('preparation:notes_view', plan_id=plan_id, session_id=session_id, note_id=note.pk)
    return render(request, 'preparation/notes_generator.html', {
        'plan': session.plan, 'session': session, 'topic': topic, 'form': form,
    })


@login_required
def notes_view(request, plan_id, session_id, note_id):
    _student(request)
    session = get_object_or_404(
        LearningSession.objects.select_related('plan', 'topic__learning_topic'),
        pk=session_id, plan__pk=plan_id, plan__student=request.user,
    )
    if not session.topic_id or not session.topic.learning_topic_id:
        raise Http404
    note = get_object_or_404(
        AINote.objects.select_related('topic'), pk=note_id, student=request.user,
        topic=session.topic.learning_topic,
    )
    return render(request, 'preparation/generated_notes.html', {
        'note': note, 'preparation_plan': session.plan,
        'preparation_session': session,
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
def start_session(request, plan_id, session_id):
    _student(request)
    with transaction.atomic():
        plan = get_object_or_404(PreparationPlan.objects.select_for_update(), pk=plan_id, student=request.user)
        session = get_object_or_404(LearningSession.objects.select_for_update(), pk=session_id, plan=plan)
        first_pending = plan.sessions.filter(status__in=[LearningSession.Status.PENDING, LearningSession.Status.MISSED]).order_by('scheduled_at', 'id').first()
        if session.status not in (LearningSession.Status.PENDING, LearningSession.Status.MISSED) or first_pending != session:
            messages.error(request, 'Complete the current task before starting another one.')
        elif not session.started_at:
            session.started_at = timezone.now()
            session.history = [*session.history, {'kind': 'started', 'at': session.started_at.isoformat()}]
            update_fields = ['started_at', 'history']
            if session.status == LearningSession.Status.MISSED:
                session.status = LearningSession.Status.PENDING
                session.history = [*session.history, {'kind': 'resumed', 'at': session.started_at.isoformat()}]
                update_fields.append('status')
            session.save(update_fields=update_fields)
            messages.success(request, 'Task started. You can complete it when you are ready.')
    return redirect('preparation:detail', plan_id=plan_id)


@login_required
@require_POST
def complete_session(request, plan_id, session_id):
    _student(request)
    with transaction.atomic():
        plan = get_object_or_404(PreparationPlan.objects.select_for_update(), pk=plan_id, student=request.user)
        session = get_object_or_404(LearningSession.objects.select_for_update(), pk=session_id, plan=plan)
        first_pending = plan.sessions.filter(status__in=[LearningSession.Status.PENDING, LearningSession.Status.MISSED]).order_by('scheduled_at', 'id').first()
        if session.status not in (LearningSession.Status.PENDING, LearningSession.Status.MISSED) or first_pending != session:
            messages.error(request, 'Complete tasks in order, starting with the current task.')
        elif not session.started_at:
            messages.error(request, 'Start this task before marking it complete.')
        else:
            session.status = LearningSession.Status.COMPLETED
            session.completed_at = timezone.now()
            session.history = [*session.history, {'kind': 'completed', 'at': session.completed_at.isoformat()}]
            session.save(update_fields=['status', 'completed_at', 'history'])
            record_readiness(session.plan, 'A learning activity was completed.')
            if session.topic and session.topic.completed_at is None:
                session.topic.completed_at = session.completed_at
                session.topic.save(update_fields=['completed_at'])
            messages.success(request, 'Task completed. Your next task is now unlocked.')
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
    first_pending = session.plan.sessions.filter(status__in=[LearningSession.Status.PENDING, LearningSession.Status.MISSED]).order_by('scheduled_at', 'id').first()
    if session.status not in (LearningSession.Status.PENDING, LearningSession.Status.MISSED) or first_pending != session or not session.started_at:
        messages.error(request, 'Start the current task before opening its practice quiz.')
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
    return redirect('preparation:quiz_take', quiz_id=quiz.pk)


@login_required
def quiz_take(request, quiz_id):
    _student(request)
    quiz = get_object_or_404(Quiz, pk=quiz_id, student=request.user, preparation_plan__isnull=False)
    if quiz.completed:
        return redirect('preparation:quiz_result', quiz_id=quiz.pk)
    answers = quiz.answers.select_related('question').order_by('id')
    return render(request, 'student/quiz.html', {'quiz': quiz, 'answers': answers})


@login_required
@require_POST
def quiz_submit(request, quiz_id):
    _student(request)
    quiz = get_object_or_404(Quiz, pk=quiz_id, student=request.user, preparation_plan__isnull=False)
    if not quiz.completed:
        answer_map = {
            str(answer.question_id): request.POST.get(f'question_{answer.question_id}', '')
            for answer in quiz.answers.all()
        }
        submit_quiz(quiz, answer_map)
        update_preparation_recommendations(quiz)
        messages.success(request, 'Quiz submitted and evaluated. Your preparation progress has been updated.')
    return redirect('preparation:quiz_result', quiz_id=quiz.pk)


@login_required
def quiz_result(request, quiz_id):
    _student(request)
    quiz = get_object_or_404(
        Quiz.objects.select_related('preparation_plan', 'preparation_topic'),
        pk=quiz_id, student=request.user, preparation_plan__isnull=False,
    )
    if not quiz.completed:
        return redirect('preparation:quiz_take', quiz_id=quiz.pk)
    answers = quiz.answers.select_related('question').order_by('id')
    return render(request, 'student/quiz_result.html', {'quiz': quiz, 'answers': answers})


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
