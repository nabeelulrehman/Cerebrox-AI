import json

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET

from apps.accounts.permissions import student_required, verified_teacher_required
from apps.ai_engine.gemini_client import AIUnavailableError
from apps.classrooms.models import Classroom, Enrollment
from . import services
from .forms import ClassQuizForm, ClassQuestionForm, AIGenerateForm, QuizScheduleForm
from .models import ClassQuiz, ClassQuestion, QuizAttempt, AttemptAnswer
from .reporting import quiz_report
from .services import QuizAccessError


def _own_quiz(request, quiz_id):
    return get_object_or_404(ClassQuiz.objects.select_related('classroom'), pk=quiz_id,
                             classroom__teacher=request.user)


def _share_link(request, quiz):
    base = getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/').rstrip('/')
    return base + reverse('class_quizzes:entry', args=[quiz.share_token])


def _teacher_quiz_detail_context(request, quiz, schedule_form=None):
    return {
        'quiz': quiz,
        'questions': quiz.questions.all(),
        'q_form': ClassQuestionForm(),
        'ai_form': AIGenerateForm(initial={'topic': quiz.title, 'level': 'Undergraduate'}),
        'share_link': _share_link(request, quiz),
        'has_attempts': quiz.attempts.exists(),
        'attempt_count': quiz.attempts.count(),
        'schedule_form': schedule_form or QuizScheduleForm(instance=quiz),
        'opening_local': timezone.localtime(quiz.start_at) if quiz.start_at else None,
        'closing_local': timezone.localtime(quiz.end_at) if quiz.end_at else None,
    }


# ======================= Teacher =======================

@verified_teacher_required
def quiz_create(request, class_id):
    classroom = get_object_or_404(Classroom, pk=class_id, teacher=request.user)
    form = ClassQuizForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        quiz = form.save(commit=False)
        quiz.classroom, quiz.created_by = classroom, request.user
        quiz.save()
        messages.success(request, 'Quiz created. Add questions, then publish it.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    return render(request, 'teacher/quiz_form.html', {'form': form, 'classroom': classroom})


@verified_teacher_required
def teacher_detail(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    return render(
        request,
        'teacher/quiz_detail.html',
        _teacher_quiz_detail_context(request, quiz),
    )


@verified_teacher_required
@require_POST
def schedule_edit(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    if quiz.status != ClassQuiz.Status.PUBLISHED:
        messages.error(request, 'Only published quizzes can have their schedule edited here.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)

    has_attempts = quiz.attempts.exists()
    form = QuizScheduleForm(request.POST, instance=quiz)
    if form.is_valid() and has_attempts and not form.cleaned_data['confirm_attempts']:
        form.add_error(
            'confirm_attempts',
            'Confirm that you understand existing student attempts and results will be preserved.',
        )
    if form.is_valid():
        form.save()
        messages.success(request, 'Quiz schedule updated. Existing student attempts and results were preserved.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)

    return render(
        request,
        'teacher/quiz_detail.html',
        _teacher_quiz_detail_context(request, quiz, schedule_form=form),
    )


@verified_teacher_required
@require_POST
def question_add(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    if quiz.attempts.exists():
        messages.error(request, 'Questions cannot be changed after students have started attempts.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    form = ClassQuestionForm(request.POST)
    if form.is_valid():
        q = form.save(commit=False)
        q.quiz, q.order = quiz, quiz.questions.count() + 1
        q.save()
        messages.success(request, 'Question added.')
    else:
        messages.error(request, 'Could not add question: ' + ' '.join(
            e for errs in form.errors.values() for e in errs))
    return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)


@verified_teacher_required
@require_POST
def question_delete(request, question_id):
    q = get_object_or_404(ClassQuestion, pk=question_id, quiz__classroom__teacher=request.user)
    if q.quiz.attempts.exists():
        messages.error(request, 'Questions cannot be removed after students have started attempts.')
    else:
        q.delete()
        messages.info(request, 'Question removed.')
    return redirect('class_quizzes:teacher_detail', quiz_id=q.quiz_id)


@verified_teacher_required
@require_POST
def ai_generate(request, quiz_id):
    from apps.ai_engine.teacher_ai import generate_mcqs_strict
    quiz = _own_quiz(request, quiz_id)
    if quiz.attempts.exists():
        messages.error(request, 'Questions cannot be changed after students have started attempts.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    form = AIGenerateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Please check the AI generation form.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    d = form.cleaned_data
    try:
        items = generate_mcqs_strict(quiz.classroom.subject or quiz.classroom.name, d['topic'],
                                     d['level'], d['difficulty'], d['count'])
    except AIUnavailableError as exc:
        messages.error(request, f'{exc} No placeholder questions were added.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    start = quiz.questions.count()
    for i, it in enumerate(items, 1):
        ClassQuestion.objects.create(quiz=quiz, order=start + i, kind=ClassQuestion.Kind.MCQ, text=it['question'],
                                     option_a=it['option_a'], option_b=it['option_b'], option_c=it['option_c'],
                                     option_d=it['option_d'], correct_answer=it['correct_answer'],
                                     explanation=it['explanation'], ai_generated=True)
    messages.success(request, f'{len(items)} AI-generated questions added. Review them before publishing.')
    return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)


@verified_teacher_required
@require_POST
def set_status(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    target = request.POST.get('status')
    if target == ClassQuiz.Status.PUBLISHED:
        if not quiz.questions.exists():
            messages.error(request, 'Add at least one question before publishing.')
            return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
        if not quiz.classroom.is_active:
            messages.error(request, 'The class is inactive.')
            return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    elif target not in (ClassQuiz.Status.DRAFT, ClassQuiz.Status.CLOSED):
        raise Http404
    if target == ClassQuiz.Status.DRAFT and quiz.attempts.exists():
        messages.error(request, 'A quiz with attempts cannot go back to draft; close it instead.')
        return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)
    quiz.status = target
    quiz.save(update_fields=['status'])
    services.audit('status_changed', quiz=quiz, actor=request.user, status=target)
    messages.success(request, f'Quiz is now {quiz.get_status_display().lower()}.')
    return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)


@verified_teacher_required
@require_POST
def email_link(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    if quiz.status != ClassQuiz.Status.PUBLISHED:
        messages.error(request, 'Publish the quiz before emailing the link.')
    else:
        try:
            n = services.email_quiz_link(quiz, _share_link(request, quiz))
            messages.success(request, f'Link emailed to {n} enrolled student(s).')
        except Exception:                       # SMTP misconfiguration etc. — never claim success
            messages.error(request, 'Email could not be sent. Check the SMTP settings (EMAIL_* variables).')
    return redirect('class_quizzes:teacher_detail', quiz_id=quiz.id)


@verified_teacher_required
def report(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    for a in QuizAttempt.objects.filter(quiz=quiz, status__in=[QuizAttempt.Status.IN_PROGRESS, QuizAttempt.Status.PAUSED]):
        services.finalize_if_overdue(a)
    return render(request, 'teacher/quiz_report.html', {'quiz': quiz, 'report': quiz_report(quiz)})


@verified_teacher_required
def attempt_review(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt.objects.select_related('quiz__classroom', 'student'),
                                pk=attempt_id, quiz__classroom__teacher=request.user,
                                status=QuizAttempt.Status.SUBMITTED)
    answers = attempt.answers.select_related('question').order_by('question__order')
    return render(request, 'teacher/attempt_review.html', {'attempt': attempt, 'answers': answers})


@verified_teacher_required
@require_POST
def grade_answer(request, answer_id):
    try:
        ans = services.teacher_grade_answer(request.user, answer_id, request.POST.get('marks', ''),
                                            request.POST.get('feedback', ''))
        messages.success(request, 'Marks saved.')
        return redirect('class_quizzes:attempt_review', attempt_id=ans.attempt_id)
    except (QuizAccessError, ArithmeticError, ValueError) as exc:
        messages.error(request, str(exc) if isinstance(exc, QuizAccessError) else 'Enter a valid number of marks.')
        ans = AttemptAnswer.objects.filter(pk=answer_id, attempt__quiz__classroom__teacher=request.user).first()
        if ans is None:
            raise Http404
        return redirect('class_quizzes:attempt_review', attempt_id=ans.attempt_id)


@verified_teacher_required
@require_POST
def ai_suggest_view(request, answer_id):
    try:
        ans = services.ai_suggest(request.user, answer_id)
        messages.info(request, 'AI suggestion saved. It is NOT applied until you approve it.')
    except QuizAccessError:
        raise Http404
    except AIUnavailableError as exc:
        ans = AttemptAnswer.objects.get(pk=answer_id)
        messages.error(request, str(exc))
    return redirect('class_quizzes:attempt_review', attempt_id=ans.attempt_id)


@verified_teacher_required
@require_POST
def accept_ai(request, answer_id):
    ans = AttemptAnswer.objects.filter(pk=answer_id, attempt__quiz__classroom__teacher=request.user).first()
    if ans is None or ans.ai_suggested_marks is None:
        raise Http404
    services.teacher_grade_answer(request.user, answer_id, ans.ai_suggested_marks,
                                  request.POST.get('feedback') or ans.ai_feedback)
    messages.success(request, 'You approved the suggested marks.')
    return redirect('class_quizzes:attempt_review', attempt_id=ans.attempt_id)


@verified_teacher_required
@require_POST
def release(request, quiz_id):
    quiz = _own_quiz(request, quiz_id)
    n = services.release_results(request.user, quiz)
    messages.success(request, f'Results released for {n} graded attempt(s).')
    return redirect('class_quizzes:report', quiz_id=quiz.id)


@verified_teacher_required
def scanner(request):
    return render(request, 'teacher/scanner.html')


@verified_teacher_required
@require_POST
def verify(request):
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        payload = {}
    try:
        attempt = services.verify_token(request.user, payload.get('token', ''))
    except QuizAccessError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'student': str(attempt.student), 'quiz': attempt.quiz.title,
                         'resumed': attempt.pause_count > 0})


# ======================= Student =======================

@student_required
def my_quizzes(request):
    class_ids = Enrollment.objects.filter(student=request.user, status=Enrollment.Status.ACTIVE) \
        .values_list('classroom_id', flat=True)
    quizzes = ClassQuiz.objects.filter(classroom_id__in=class_ids, status=ClassQuiz.Status.PUBLISHED) \
        .select_related('classroom')
    attempts = QuizAttempt.objects.filter(student=request.user, quiz__in=quizzes)
    return render(request, 'student/class_quizzes.html', {'quizzes': quizzes, 'attempts': attempts})


@student_required
def entry(request, token):
    quiz = get_object_or_404(ClassQuiz.objects.select_related('classroom__teacher'), share_token=token)
    if quiz.status != ClassQuiz.Status.PUBLISHED:
        raise Http404
    reason = None
    enrollment_denied = False
    try:
        services.check_can_start(request.user, quiz)
        if not QuizAttempt.objects.filter(quiz=quiz, student=request.user, status__in=services.OPEN_STATUSES).exists():
            used = QuizAttempt.objects.filter(quiz=quiz, student=request.user,
                                              status=QuizAttempt.Status.SUBMITTED).count()
            if used >= quiz.max_attempts:
                reason = 'You have used all attempts for this quiz.'
    except QuizAccessError as exc:
        reason = str(exc)
        enrollment_denied = reason == 'You are not enrolled in the class assigned to this quiz.'
    open_attempt = None if enrollment_denied else QuizAttempt.objects.filter(
        quiz=quiz, student=request.user, status__in=services.OPEN_STATUSES,
    ).first()
    return render(request, 'student/class_quiz_entry.html',
                  {'quiz': quiz, 'reason': reason, 'open_attempt': open_attempt,
                   'enrollment_denied': enrollment_denied})


@student_required
@require_POST
def start(request, token):
    quiz = get_object_or_404(ClassQuiz, share_token=token)
    try:
        attempt = services.start_or_resume_attempt(request.user, quiz)
    except QuizAccessError as exc:
        if str(exc) == 'This quiz is not available.':
            raise Http404
        messages.error(request, str(exc))
        return redirect('class_quizzes:entry', token=token)
    return redirect('class_quizzes:take', attempt_id=attempt.id)


def _own_attempt(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt.objects.select_related('quiz__classroom'),
                                pk=attempt_id, student=request.user)
    try:
        services.check_enrolled_only(request.user, attempt.quiz)
    except QuizAccessError:
        raise Http404
    if attempt.status != QuizAttempt.Status.SUBMITTED and attempt.quiz.status != ClassQuiz.Status.PUBLISHED:
        raise Http404
    services.finalize_if_overdue(attempt)
    attempt.refresh_from_db()
    return attempt


@student_required
def take(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    if attempt.status == QuizAttempt.Status.SUBMITTED:
        return redirect('class_quizzes:result', attempt_id=attempt.id)
    if attempt.status in (QuizAttempt.Status.AWAITING, QuizAttempt.Status.PAUSED):
        raw = services.issue_token(attempt)
        return render(request, 'student/class_quiz_verify.html', {
            'attempt': attempt, 'qr': services.qr_svg(raw), 'raw': raw,
            'ttl': int(services.TOKEN_TTL.total_seconds()),
            'paused': attempt.status == QuizAttempt.Status.PAUSED})
    answers = {a.question_id: a for a in attempt.answers.all()}
    questions = {q.id: q for q in attempt.quiz.questions.all()}
    rows = [(questions[qid], answers.get(qid)) for qid in attempt.question_order if qid in questions]
    remaining = max(0, int((attempt.deadline_at - timezone.now()).total_seconds()))
    return render(request, 'student/class_quiz_take.html', {'attempt': attempt, 'rows': rows, 'remaining': remaining})


@student_required
@require_GET
def status(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    remaining = max(0, int((attempt.deadline_at - timezone.now()).total_seconds())) if attempt.deadline_at else None
    return JsonResponse({'status': attempt.status, 'remaining': remaining})


@student_required
@require_POST
def save(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    try:
        services.save_answers(attempt, request.POST)
    except QuizAccessError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=409)
    return JsonResponse({'ok': True})


@student_required
@require_POST
def event(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    if attempt.status == QuizAttempt.Status.SUBMITTED:
        return JsonResponse({'status': attempt.status})
    attempt = services.report_event(attempt, request.POST.get('kind', ''))
    return JsonResponse({'status': attempt.status})


@student_required
@require_POST
def submit(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    services.submit_attempt(attempt, request.POST)
    messages.success(request, 'Submitted.')
    return redirect('class_quizzes:result', attempt_id=attempt.id)


@student_required
def result(request, attempt_id):
    attempt = _own_attempt(request, attempt_id)
    if attempt.status != QuizAttempt.Status.SUBMITTED:
        return redirect('class_quizzes:take', attempt_id=attempt.id)
    visible = services.student_can_see_result(attempt)
    answers = attempt.answers.select_related('question').order_by('question__order') if visible else []
    return render(request, 'student/class_quiz_result.html',
                  {'attempt': attempt, 'visible': visible, 'answers': answers})
