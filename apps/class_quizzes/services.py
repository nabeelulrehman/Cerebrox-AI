"""
Server-side rules for teacher-managed quizzes: eligibility, attempts, QR verification,
pause/resume, answer persistence, deadline enforcement and grading.

The browser is never trusted for time or authorization: deadline_at is set by the server
and answers arriving after it (plus a small grace for network latency) are ignored.
"""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.classrooms.services import is_active_member
from .models import (ClassQuiz, ClassQuestion, QuizAttempt, AttemptAnswer, VerificationToken, AuditLog)

SUBMIT_GRACE = timedelta(seconds=getattr(settings, 'QUIZ_SUBMIT_GRACE_SECONDS', 10))
TOKEN_TTL = timedelta(seconds=getattr(settings, 'QUIZ_QR_TTL_SECONDS', 120))
OPEN_STATUSES = (QuizAttempt.Status.AWAITING, QuizAttempt.Status.IN_PROGRESS, QuizAttempt.Status.PAUSED)


class QuizAccessError(Exception):
    """Message is safe to show to the student/teacher."""


def _schedule_time(value):
    if value is not None and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def audit(action, quiz=None, attempt=None, actor=None, **detail):
    AuditLog.objects.create(quiz=quiz or (attempt.quiz if attempt else None), attempt=attempt,
                            actor=actor, action=action, detail=detail)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------- eligibility

def check_can_start(user, quiz, now=None):
    """Raises QuizAccessError unless this user may begin/continue a NEW attempt right now."""
    now = _schedule_time(timezone.now() if now is None else now)
    if not (user.is_authenticated and user.is_student):
        raise QuizAccessError('Only student accounts can take class quizzes.')
    if quiz.status != ClassQuiz.Status.PUBLISHED:
        raise QuizAccessError('This quiz is not available.')
    if not is_active_member(user, quiz.classroom):
        raise QuizAccessError('You are not enrolled in the class assigned to this quiz.')
    start_at = _schedule_time(quiz.start_at)
    end_at = _schedule_time(quiz.end_at)
    if start_at and now < start_at:
        raise QuizAccessError('This quiz has not opened yet.')
    if end_at and now >= end_at:
        raise QuizAccessError('This quiz has ended.')


def finalize_if_overdue(attempt, now=None):
    now = _schedule_time(timezone.now() if now is None else now)
    if attempt.status in (QuizAttempt.Status.IN_PROGRESS, QuizAttempt.Status.PAUSED) \
            and attempt.deadline_at and now > attempt.deadline_at + SUBMIT_GRACE:
        submit_attempt(attempt, None, now=now, late=True)
        audit('auto_submitted_deadline', attempt=attempt)
    return attempt


def _deadline_for(quiz, start):
    start = _schedule_time(start)
    deadline = start + timedelta(minutes=quiz.duration_minutes)
    end_at = _schedule_time(quiz.end_at)
    if end_at and deadline > end_at:
        deadline = end_at
    return deadline


@transaction.atomic
def start_or_resume_attempt(student, quiz):
    now = timezone.now()
    open_attempt = (QuizAttempt.objects.select_for_update()
                    .filter(quiz=quiz, student=student, status__in=OPEN_STATUSES).first())
    if open_attempt:
        check_enrolled_only(student, quiz)
        return open_attempt
    check_can_start(student, quiz, now)
    used = QuizAttempt.objects.filter(quiz=quiz, student=student, status=QuizAttempt.Status.SUBMITTED).count()
    if used >= quiz.max_attempts:
        raise QuizAccessError('You have used all attempts for this quiz.')
    order = list(quiz.questions.values_list('id', flat=True))
    if not order:
        raise QuizAccessError('This quiz has no questions yet.')
    if quiz.shuffle_questions:
        secrets.SystemRandom().shuffle(order)
    kwargs = dict(quiz=quiz, student=student, number=used + 1, question_order=order,
                  total_marks=quiz.total_marks)
    if quiz.supervised:
        kwargs['status'] = QuizAttempt.Status.AWAITING
    else:
        kwargs.update(status=QuizAttempt.Status.IN_PROGRESS, started_at=now, deadline_at=_deadline_for(quiz, now))
    try:
        with transaction.atomic():
            attempt = QuizAttempt.objects.create(**kwargs)
    except IntegrityError:          # a concurrent request created it first
        return QuizAttempt.objects.get(quiz=quiz, student=student, status__in=OPEN_STATUSES)
    AttemptAnswer.objects.bulk_create([AttemptAnswer(attempt=attempt, question_id=qid) for qid in order])
    audit('attempt_created', attempt=attempt, actor=student, supervised=quiz.supervised)
    return attempt


def check_enrolled_only(student, quiz):
    if not (student.is_student and is_active_member(student, quiz.classroom)):
        raise QuizAccessError('You are not enrolled in the class assigned to this quiz.')


# ---------------------------------------------------------------- QR verification

@transaction.atomic
def issue_token(attempt):
    """Invalidate older unused tokens, then mint a fresh single-use token. Returns the RAW token."""
    if attempt.status not in (QuizAttempt.Status.AWAITING, QuizAttempt.Status.PAUSED):
        raise QuizAccessError('This attempt does not need verification.')
    now = timezone.now()
    VerificationToken.objects.filter(attempt=attempt, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
    raw = secrets.token_urlsafe(32)
    VerificationToken.objects.create(attempt=attempt, token_hash=_hash(raw), expires_at=now + TOKEN_TTL)
    audit('token_issued', attempt=attempt, actor=attempt.student)
    return raw


def verify_token(teacher, raw_token):
    """Teacher scans a student's QR. Returns the authorized attempt or raises QuizAccessError.
    Every failure path returns the SAME message so tokens can't be probed."""
    generic = QuizAccessError('Invalid or expired code. Ask the student to refresh their QR.')
    raw_token = (raw_token or '').strip()
    if not raw_token or len(raw_token) > 200:
        raise generic
    with transaction.atomic():
        token = (VerificationToken.objects.select_related('attempt__quiz__classroom')
                 .filter(token_hash=_hash(raw_token)).first())
        if token is None:
            raise generic
        attempt = token.attempt
        quiz = attempt.quiz
        # authorization BEFORE consuming, so a wrong teacher cannot burn a student's token
        if not (teacher.is_authenticated and teacher.is_verified_teacher
                and quiz.classroom.teacher_id == teacher.id):
            audit('verify_denied', attempt=attempt, actor=teacher)
            raise generic
        now = timezone.now()
        # atomic single-use consume: exactly one concurrent scanner can win this UPDATE
        won = VerificationToken.objects.filter(pk=token.pk, used_at__isnull=True, expires_at__gt=now) \
            .update(used_at=now, used_by=teacher)
        if won != 1:
            raise generic
        attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status not in (QuizAttempt.Status.AWAITING, QuizAttempt.Status.PAUSED):
            raise generic
        if quiz.status != ClassQuiz.Status.PUBLISHED or not is_active_member(attempt.student, quiz.classroom):
            raise QuizAccessError('This quiz or enrollment is no longer active.')
        if attempt.started_at is None:
            end_at = _schedule_time(quiz.end_at)
            if end_at and now >= end_at:
                raise QuizAccessError('This quiz has ended.')
            attempt.started_at = now
            attempt.deadline_at = _deadline_for(quiz, now)   # clock starts at FIRST authorization only
        attempt.status = QuizAttempt.Status.IN_PROGRESS
        attempt.save(update_fields=['status', 'started_at', 'deadline_at'])
        audit('verified', attempt=attempt, actor=teacher)
        return attempt


@transaction.atomic
def report_event(attempt, kind):
    """Browser reported a suspicious event (tab hidden, window blur, fullscreen exit).
    Supervised quizzes pause until the teacher re-verifies; the deadline keeps running.
    This is a deterrent, NOT proof of cheating or a guarantee of prevention."""
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
    kind = (kind or 'unknown')[:30]
    audit('browser_event', attempt=attempt, actor=attempt.student, kind=kind)
    if attempt.quiz.supervised and attempt.status == QuizAttempt.Status.IN_PROGRESS:
        attempt.status = QuizAttempt.Status.PAUSED
        attempt.pause_count += 1
        attempt.save(update_fields=['status', 'pause_count'])
        audit('paused', attempt=attempt, kind=kind)
    return attempt


# ---------------------------------------------------------------- answers

def _apply_answers(attempt, data):
    questions = {q.id: q for q in attempt.quiz.questions.all()}
    for ans in attempt.answers.all():
        q = questions.get(ans.question_id)
        if q is None:
            continue
        key = f'q_{q.id}'
        if key not in data:
            continue
        value = (data.get(key) or '')
        if q.kind == ClassQuestion.Kind.MCQ:
            value = value.strip().upper()
            ans.selected = value if value in ('A', 'B', 'C', 'D') else ''
        else:
            ans.text_answer = value[:10000]
        ans.save(update_fields=['selected', 'text_answer'])


@transaction.atomic
def save_answers(attempt, data):
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
    now = timezone.now()
    if attempt.status != QuizAttempt.Status.IN_PROGRESS:
        raise QuizAccessError('This attempt is not active.')
    if now > attempt.deadline_at:
        raise QuizAccessError('Time is up.')
    _apply_answers(attempt, data)
    return attempt


@transaction.atomic
def submit_attempt(attempt, data, now=None, late=False):
    attempt = QuizAttempt.objects.select_for_update().select_related('quiz').get(pk=attempt.pk)
    if attempt.status == QuizAttempt.Status.SUBMITTED:
        return attempt                                   # idempotent: double-submit is harmless
    now = now or timezone.now()
    on_time = attempt.status == QuizAttempt.Status.IN_PROGRESS and attempt.deadline_at \
        and now <= attempt.deadline_at + SUBMIT_GRACE
    if data is not None and on_time and not late:
        _apply_answers(attempt, data)
    attempt.submitted_late = bool(late or (attempt.deadline_at and now > attempt.deadline_at))
    attempt.status = QuizAttempt.Status.SUBMITTED
    attempt.submitted_at = min(now, attempt.deadline_at) if attempt.deadline_at and attempt.submitted_late else now
    attempt.save(update_fields=['status', 'submitted_at', 'submitted_late'])
    grade_mcq(attempt)
    audit('submitted', attempt=attempt, late=attempt.submitted_late)
    return attempt


# ---------------------------------------------------------------- grading

def grade_mcq(attempt):
    quiz = attempt.quiz
    penalty = Decimal(quiz.negative_marking or 0)
    questions = {q.id: q for q in quiz.questions.all()}
    for ans in attempt.answers.all():
        q = questions[ans.question_id]
        if q.kind != ClassQuestion.Kind.MCQ:
            continue
        if not ans.selected:
            marks = Decimal('0')
        elif ans.selected == q.correct_answer:
            marks = q.marks
        else:
            marks = -(q.marks * penalty)
        ans.auto_marks = marks
        ans.awarded_marks = marks
        ans.reviewed = True
        ans.save(update_fields=['auto_marks', 'awarded_marks', 'reviewed'])
    recompute_attempt(attempt)


def recompute_attempt(attempt):
    answers = list(attempt.answers.select_related('question'))
    attempt.total_marks = sum((a.question.marks for a in answers), Decimal('0'))
    attempt.auto_score = sum((a.auto_marks or Decimal('0') for a in answers), Decimal('0'))
    pending = [a for a in answers if a.question.kind == ClassQuestion.Kind.DESCRIPTIVE and not a.reviewed]
    attempt.grading_complete = not pending
    total = sum((a.awarded_marks or Decimal('0') for a in answers), Decimal('0'))
    attempt.final_score = max(total, Decimal('0')) if attempt.grading_complete else None
    attempt.save(update_fields=['total_marks', 'auto_score', 'grading_complete', 'final_score'])


def _own_answer(teacher, answer_id):
    ans = (AttemptAnswer.objects.select_related('attempt__quiz__classroom', 'question')
           .filter(pk=answer_id, attempt__quiz__classroom__teacher=teacher,
                   attempt__status=QuizAttempt.Status.SUBMITTED).first())
    if ans is None or not teacher.is_verified_teacher:
        raise QuizAccessError('Answer not found.')
    return ans


@transaction.atomic
def teacher_grade_answer(teacher, answer_id, marks, feedback=''):
    ans = _own_answer(teacher, answer_id)
    if ans.question.kind != ClassQuestion.Kind.DESCRIPTIVE:
        raise QuizAccessError('Multiple-choice answers are graded automatically.')
    marks = Decimal(str(marks))
    if marks < 0 or marks > ans.question.marks:
        raise QuizAccessError(f'Marks must be between 0 and {ans.question.marks}.')
    ans.awarded_marks, ans.reviewed, ans.teacher_feedback = marks, True, feedback[:2000]
    ans.save(update_fields=['awarded_marks', 'reviewed', 'teacher_feedback'])
    recompute_attempt(ans.attempt)
    audit('graded_answer', attempt=ans.attempt, actor=teacher, answer=ans.pk, marks=str(marks))
    return ans


def ai_suggest(teacher, answer_id):
    """Stores an AI SUGGESTION. awarded_marks/reviewed are untouched — the teacher must approve."""
    from apps.ai_engine.teacher_ai import suggest_descriptive_grade
    ans = _own_answer(teacher, answer_id)
    if ans.question.kind != ClassQuestion.Kind.DESCRIPTIVE:
        raise QuizAccessError('AI suggestions are only for descriptive answers.')
    result = suggest_descriptive_grade(ans.question.text, ans.question.model_answer,
                                       ans.text_answer, float(ans.question.marks))
    ans.ai_suggested_marks, ans.ai_feedback = Decimal(str(result['marks'])), result['feedback']
    ans.save(update_fields=['ai_suggested_marks', 'ai_feedback'])
    audit('ai_suggested', attempt=ans.attempt, actor=teacher, answer=ans.pk)
    return ans


@transaction.atomic
def release_results(teacher, quiz):
    if quiz.classroom.teacher_id != teacher.id:
        raise QuizAccessError('Not your quiz.')
    n = quiz.attempts.filter(status=QuizAttempt.Status.SUBMITTED, grading_complete=True).update(results_released=True)
    audit('results_released', quiz=quiz, actor=teacher, count=n)
    return n


def student_can_see_result(attempt, now=None):
    now = _schedule_time(timezone.now() if now is None else now)
    if attempt.status != QuizAttempt.Status.SUBMITTED or not attempt.grading_complete:
        return False
    if attempt.results_released:
        return True
    rule = attempt.quiz.result_visibility
    if rule == ClassQuiz.Visibility.IMMEDIATE:
        return True
    if rule == ClassQuiz.Visibility.AFTER_END:
        quiz = attempt.quiz
        end_at = _schedule_time(quiz.end_at)
        return quiz.status == ClassQuiz.Status.CLOSED or bool(end_at and now >= end_at)
    return False


# ---------------------------------------------------------------- QR image + email

def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    from io import BytesIO
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def email_quiz_link(quiz, link):
    from django.core.mail import send_mail
    from apps.classrooms.models import Enrollment
    sent = 0
    students = Enrollment.objects.filter(classroom=quiz.classroom, status=Enrollment.Status.ACTIVE) \
        .select_related('student')
    for enr in students:
        if not enr.student.email:
            continue
        send_mail(
            subject=f'New quiz: {quiz.title}',
            message=(f'Hi {enr.student.first_name or enr.student.username},\n\n'
                     f'{quiz.classroom.teacher} published "{quiz.title}" for {quiz.classroom.name}.\n'
                     f'Log in and open: {link}\n\nThe link only works for students enrolled in the class.'),
            from_email=None, recipient_list=[enr.student.email], fail_silently=False)
        sent += 1
    audit('emailed_link', quiz=quiz, actor=quiz.created_by, count=sent)
    return sent
