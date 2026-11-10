import mimetypes
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import admin_required, verified_teacher_required
from apps.teachers.models import TeacherProfile
from . import services
from .forms import (
    GuidanceComposeForm, GuidanceForm, GuidanceReplyForm, ResourceForm, ReviewForm, SessionForm,
)
from .models import (
    GuidanceMessage, Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking,
    SessionAttendance, Review,
)
from .services import MarketError


def _student(request):
    if not request.user.is_student:
        raise PermissionDenied


def _approved_teacher_or_404(teacher_profile_id):
    return get_object_or_404(TeacherProfile.objects.select_related('user'), pk=teacher_profile_id,
                             verification_status=TeacherProfile.Status.APPROVED)


# ---------------- materials ----------------

def public_resources(request):
    qs = Resource.objects.filter(visibility__in=[Resource.Visibility.PUBLIC, Resource.Visibility.PAID],
                                 teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED)
    return render(request, 'marketplace/resources.html', {'resources': qs.select_related('teacher')})


@verified_teacher_required
def teacher_resources(request):
    form = ResourceForm(request.POST or None, request.FILES or None, teacher=request.user)
    if request.method == 'POST' and form.is_valid():
        r = form.save(commit=False)
        r.teacher, r.original_name = request.user, form.cleaned_data['file'].name[:255]
        r.save()
        form.save_m2m()
        messages.success(request, 'Resource saved.')
        return redirect('market:teacher_resources')
    return render(request, 'marketplace/teacher_resources.html',
                  {'form': form, 'resources': Resource.objects.filter(teacher=request.user)})


@verified_teacher_required
@require_POST
def resource_delete(request, resource_id):
    r = get_object_or_404(Resource, pk=resource_id, teacher=request.user)
    r.file.delete(save=False)
    r.delete()
    messages.info(request, 'Resource deleted.')
    return redirect('market:teacher_resources')


@login_required
def resource_download(request, resource_id):
    r = get_object_or_404(Resource, pk=resource_id)
    if not services.can_access_resource(request.user, r):
        raise Http404            # 404, not 403: don't reveal that a private file exists
    resp = FileResponse(r.file.open('rb'), as_attachment=True, filename=r.original_name or 'download')
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'private, no-store'
    return resp


@login_required
@require_POST
def resource_buy(request, resource_id):
    _student(request)
    r = get_object_or_404(Resource, pk=resource_id, visibility=Resource.Visibility.PAID)
    try:
        p = services.request_purchase(request.user, r)
        messages.info(request, f'Purchase request recorded (reference {p.reference}). Access unlocks only after the '
                               'payment is verified and confirmed by an administrator.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:resources')


# ---------------- guidance ----------------

@login_required
@require_POST
def guidance_request(request, teacher_id):
    _student(request)
    t = _approved_teacher_or_404(teacher_id)
    form = GuidanceForm(request.POST)
    if form.is_valid():
        g = form.save(commit=False)
        g.student, g.teacher, g.started_by = request.user, t.user, request.user
        g.save()
        messages.success(request, 'Your message was sent. The teacher can reply here in the app.')
    return redirect('teachers:public_profile', teacher_id=t.id)


def _guidance_context(user, form=None):
    if user.is_student:
        conversations = GuidanceRequest.objects.filter(student=user)
    elif user.is_verified_teacher:
        conversations = GuidanceRequest.objects.filter(teacher=user)
    else:
        raise PermissionDenied
    conversations = conversations.select_related(
        'student', 'teacher', 'started_by',
    ).prefetch_related(Prefetch(
        'messages',
        queryset=GuidanceMessage.objects.select_related('sender').order_by('sent_at', 'pk'),
    ))
    return {
        'items': conversations,
        'compose_form': form if form is not None else GuidanceComposeForm(user=user),
        'selected_recipient': form.cleaned_data.get('recipient') if form and form.is_bound else None,
        'is_teacher': user.is_verified_teacher,
    }


def _render_guidance(request, form=None, status=200):
    template = 'marketplace/inbox.html' if request.user.is_verified_teacher else 'marketplace/my_guidance.html'
    return render(request, template, _guidance_context(request.user, form), status=status)


@login_required
@require_GET
def guidance_recipient_search(request):
    user = request.user
    if not user.is_student and not user.is_verified_teacher:
        raise PermissionDenied

    query = request.GET.get('q', '').strip()[:100]
    matches = GuidanceComposeForm.recipient_queryset(user)
    if len(query) < 2:
        matches = matches.none()
    else:
        for term in query.split():
            matches = matches.filter(
                Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(username__icontains=term)
                | Q(email__icontains=term)
            )
    results = []
    for recipient in matches[:10]:
        results.append({
            'id': recipient.pk,
            'label': recipient.get_full_name() or recipient.username,
            'detail': f'@{recipient.username}' + (f' · {recipient.email}' if recipient.email else ''),
        })
    return JsonResponse({'results': results})


@verified_teacher_required
def teacher_inbox(request):
    return _render_guidance(request)


@login_required
@require_POST
def guidance_send(request):
    if not request.user.is_student and not request.user.is_verified_teacher:
        if request.user.is_teacher:
            messages.warning(request, 'Your teacher account has not been approved yet.')
            return redirect('teachers:dashboard')
        raise PermissionDenied

    form = GuidanceComposeForm(request.POST, user=request.user)
    if not form.is_valid():
        return _render_guidance(request, form=form, status=400)

    recipient = form.cleaned_data['recipient']
    if request.user.is_student:
        student, teacher = request.user, recipient
    else:
        student, teacher = recipient, request.user
    conversation = GuidanceRequest.objects.create(
        student=student,
        teacher=teacher,
        subject=form.cleaned_data['subject'],
        message=form.cleaned_data['message'],
        started_by=request.user,
    )
    messages.success(request, 'Your message was sent.')
    return redirect(f"{reverse('market:my_guidance')}#conversation-{conversation.pk}")


def _add_guidance_reply(request, conversation, message_text):
    form = GuidanceReplyForm({'message': message_text})
    if not form.is_valid():
        messages.error(request, 'Enter a message of up to 2,000 characters before sending.')
        return False
    GuidanceMessage.objects.create(
        conversation=conversation,
        sender=request.user,
        body=form.cleaned_data['message'],
    )
    conversation.status = (
        GuidanceRequest.Status.ANSWERED if request.user.is_teacher else GuidanceRequest.Status.OPEN
    )
    conversation.save(update_fields=['status'])
    messages.success(request, 'Your reply was sent.')
    return True


@verified_teacher_required
@require_POST
def guidance_reply(request, request_id):
    g = get_object_or_404(GuidanceRequest, pk=request_id, teacher=request.user)
    _add_guidance_reply(request, g, request.POST.get('message', request.POST.get('reply', '')))
    return redirect(f"{reverse('market:teacher_inbox')}#conversation-{g.pk}")


@login_required
@require_POST
def guidance_conversation_reply(request, request_id):
    user = request.user
    if not user.is_student and not user.is_verified_teacher:
        raise PermissionDenied
    conversation = get_object_or_404(
        GuidanceRequest.objects.filter(Q(student=user) | Q(teacher=user)),
        pk=request_id,
    )
    _add_guidance_reply(request, conversation, request.POST.get('message', ''))
    return redirect(f"{reverse('market:my_guidance')}#conversation-{conversation.pk}")


@login_required
def my_guidance(request):
    return _render_guidance(request)


# ---------------- sessions ----------------

@verified_teacher_required
def teacher_sessions(request):
    now = timezone.now()
    for session in TeachingSession.objects.filter(
        teacher=request.user,
        status__in=(TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING),
        starts_at__lte=now,
    ):
        services.sync_session_status(session, now)
    sessions = TeachingSession.objects.filter(teacher=request.user).prefetch_related('bookings').order_by('-starts_at')
    for session in sessions:
        if session.livekit_egress_id and session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.STOPPING,
            TeachingSession.RecordingStatus.RECORDING,
        ):
            services.refresh_livekit_recording_state(session)
    form = SessionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        s = form.save(commit=False)
        s.teacher = request.user
        s.save()
        messages.success(request, 'Session created.')
        return redirect('market:session_detail', session_id=s.pk)
    return render(request, 'marketplace/teacher_sessions.html', {
        'form': form,
        'sessions': sessions,
        'recording_ready': services.livekit_recording_ready(),
    })


@login_required
def sessions_list(request):
    _student(request)
    now = timezone.now()
    services.send_booking_reminders(request.user)
    for session in TeachingSession.objects.filter(
        status__in=(TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING),
        starts_at__lte=now,
    ):
        services.sync_session_status(session, now)
    bookings = SessionBooking.objects.filter(student=request.user).select_related('session')
    mine = {booking.session_id: booking for booking in bookings}
    attendance_by_session = {
        record.session_id: record
        for record in SessionAttendance.objects.filter(student=request.user)
    }
    sessions = TeachingSession.objects.filter(
        Q(status=TeachingSession.Status.ONGOING)
        | Q(status=TeachingSession.Status.UPCOMING, starts_at__gt=now)
        | Q(bookings__student=request.user),
    ).filter(
        Q(teacher__teacher_profile__verification_status=TeacherProfile.Status.APPROVED)
        | Q(bookings__student=request.user),
    ).select_related('teacher', 'teacher__teacher_profile').distinct().order_by('starts_at')
    sessions = list(sessions)
    for session in sessions:
        services.sync_session_status(session, now)
        session.student_booking = mine.get(session.pk)
        session.student_attendance = attendance_by_session.get(session.pk)
        session.reminder_due = (
            session.status == TeachingSession.Status.UPCOMING
            and now < session.starts_at <= now + timedelta(hours=24)
            and session.student_booking is not None
            and session.student_booking.status == SessionBooking.Status.CONFIRMED
        )
    sessions = [
        session for session in sessions
        if session.status in (TeachingSession.Status.UPCOMING, TeachingSession.Status.ONGOING)
        or session.pk in mine
    ]
    return render(request, 'marketplace/sessions.html', {'sessions': sessions})


@login_required
def session_detail(request, session_id):
    session = get_object_or_404(
        TeachingSession.objects.select_related('teacher', 'teacher__teacher_profile'),
        pk=session_id,
    )
    booking = SessionBooking.objects.filter(session=session, student=request.user).first()
    if not (request.user.is_teacher and session.teacher_id == request.user.pk):
        _student(request)
        if (
            not hasattr(session.teacher, 'teacher_profile')
            or session.teacher.teacher_profile.verification_status != TeacherProfile.Status.APPROVED
        ) and booking is None:
            raise Http404
    services.sync_session_status(session)
    if request.user.is_student:
        services.send_booking_reminders(request.user)
    active_booking = booking and booking.status != SessionBooking.Status.CANCELLED
    can_manage = request.user.is_teacher and session.teacher_id == request.user.pk
    if can_manage and session.livekit_egress_id and session.recording_status in (
        TeachingSession.RecordingStatus.STARTING,
        TeachingSession.RecordingStatus.STOPPING,
        TeachingSession.RecordingStatus.RECORDING,
    ):
        session = services.refresh_livekit_recording_state(session)
    attendance = None
    bookings = None
    if can_manage:
        bookings = session.bookings.select_related('student').order_by('created_at')
        attendance = session.attendance_records.select_related('student').all()
    elif booking:
        attendance = SessionAttendance.objects.filter(session=session, student=request.user).first()
    can_book = (
        request.user.is_student
        and not active_booking
        and session.status == TeachingSession.Status.UPCOMING
        and session.starts_at > timezone.now()
        and session.bookings.exclude(status=SessionBooking.Status.CANCELLED).count() < session.capacity
    )
    can_cancel_booking = (
        request.user.is_student
        and active_booking
        and session.status == TeachingSession.Status.UPCOMING
        and session.starts_at > timezone.now()
    )
    reminder_due = (
        session.status == TeachingSession.Status.UPCOMING
        and timezone.now() < session.starts_at <= timezone.now() + timedelta(hours=24)
    )
    return render(request, 'marketplace/session_detail.html', {
        'session': session,
        'booking': booking,
        'active_booking': active_booking,
        'can_manage': can_manage,
        'can_view_recording': can_manage or bool(
            request.user.is_student and booking and booking.status == SessionBooking.Status.CONFIRMED
        ),
        'bookings': bookings,
        'attendance': attendance,
        'can_book': can_book,
        'can_cancel_booking': can_cancel_booking,
        'can_join': bool(
            request.user.is_student
            and active_booking
            and booking.status == SessionBooking.Status.CONFIRMED
            and session.status == TeachingSession.Status.ONGOING
        ),
        'recording_ready': services.livekit_recording_ready(),
        'reminder_due': reminder_due,
    })


def _classroom_role(request, session):
    services.sync_session_status(session)
    if session.status != TeachingSession.Status.ONGOING:
        raise MarketError('The teacher has not started this class, or it has already ended.')
    if request.user.is_teacher and session.teacher_id == request.user.pk:
        return True
    if request.user.is_student and session.bookings.filter(
        student=request.user, status=SessionBooking.Status.CONFIRMED,
    ).exists():
        return False
    raise PermissionDenied


@login_required
def session_classroom(request, session_id):
    session = get_object_or_404(
        TeachingSession.objects.select_related('teacher'),
        pk=session_id,
    )
    try:
        is_teacher = _classroom_role(request, session)
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)
    return render(request, 'marketplace/classroom.html', {
        'session': session,
        'is_teacher': is_teacher,
        'teacher_identity': services.livekit_participant_identity(session, session.teacher),
        'livekit_ready': services.livekit_ready(),
        'livekit_configuration_issues': services.livekit_configuration_issues(),
        'recording_ready': services.livekit_recording_ready(),
        'recording_issues': services.livekit_recording_configuration_issues(),
        'recording_status': session.get_recording_status_display(),
        'recording_error': session.recording_error if is_teacher else '',
    })


@login_required
@require_POST
def session_token(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        is_teacher = _classroom_role(request, session)
        token = services.issue_livekit_token(session, request.user, is_teacher=is_teacher)
    except MarketError as exc:
        return JsonResponse({'error': str(exc)}, status=503 if not services.livekit_ready() else 403)
    return JsonResponse({
        'url': settings.LIVEKIT_URL,
        'token': token,
    }, headers={'Cache-Control': 'private, no-store'})


@csrf_exempt
@require_POST
def livekit_webhook(request):
    try:
        event = services.parse_livekit_webhook(request.body, request.headers.get('Authorization', ''))
    except MarketError:
        return JsonResponse({'error': 'Invalid webhook request.'}, status=401)
    services.process_livekit_webhook(event)
    return HttpResponse(status=200)


@verified_teacher_required
@require_POST
def session_recording_start(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    if request.POST.get('recording_consent') != 'on':
        messages.error(request, 'Confirm that everyone in the class has been told and has agreed before recording.')
        return redirect('market:session_classroom', session_id=session.pk)
    try:
        services.start_livekit_recording(session)
        messages.success(request, 'Recording is starting. The status will update when LiveKit Egress confirms it.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_classroom', session_id=session.pk)


@login_required
@require_GET
def session_recording_status(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    authorized = (
        request.user.is_teacher and session.teacher_id == request.user.pk
        or request.user.is_student and session.bookings.filter(
            student=request.user, status=SessionBooking.Status.CONFIRMED,
        ).exists()
    )
    if not authorized:
        raise PermissionDenied
    if (
        request.user.is_teacher
        and session.teacher_id == request.user.pk
        and session.livekit_egress_id
        and session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.STOPPING,
        )
    ):
        session = services.refresh_livekit_recording_state(session)
    return JsonResponse({
        'status': session.recording_status,
        'label': session.get_recording_status_display(),
        'error': session.recording_error if request.user.is_teacher else '',
        'recording_ready': services.livekit_recording_ready(),
        'can_control': bool(request.user.is_teacher and session.teacher_id == request.user.pk),
    }, headers={'Cache-Control': 'private, no-store'})


@verified_teacher_required
@require_POST
def session_recording_stop(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        services.stop_livekit_recording(session)
        messages.success(request, 'Recording is stopping. It will be available after LiveKit finishes the file.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_classroom', session_id=session.pk)


@login_required
def session_recording_play(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id)
    if not (
        request.user.is_teacher and session.teacher_id == request.user.pk
        or request.user.is_student and session.bookings.filter(
            student=request.user, status=SessionBooking.Status.CONFIRMED,
        ).exists()
    ):
        raise PermissionDenied
    try:
        return redirect(services.livekit_recording_url(session))
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
def session_edit(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    services.sync_session_status(session)
    if session.status != TeachingSession.Status.UPCOMING:
        messages.error(request, 'Only upcoming sessions can be edited or rescheduled.')
        return redirect('market:session_detail', session_id=session.pk)
    original_schedule = (session.starts_at, session.duration_minutes)
    form = SessionForm(request.POST or None, instance=session)
    if request.method == 'POST' and form.is_valid():
        session = form.save(commit=False)
        session.save()
        if original_schedule != (session.starts_at, session.duration_minutes):
            services.notify_session_bookings(
                session,
                f'Session rescheduled: {session.title}',
                f'{session.title} has been rescheduled to '
                f'{timezone.localtime(session.starts_at):%Y-%m-%d %H:%M} '
                f'and will end at {timezone.localtime(session.ends_at):%H:%M}.',
            )
            messages.success(request, 'Session rescheduled; booked students were notified by email where configured.')
        else:
            messages.success(request, 'Session details updated.')
        return redirect('market:session_detail', session_id=session.pk)
    return render(request, 'marketplace/teacher_sessions.html', {
        'form': form,
        'session': session,
        'sessions': TeachingSession.objects.filter(teacher=request.user).prefetch_related('bookings'),
    })


@verified_teacher_required
@require_POST
def session_start(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        services.start_session(session)
        services.notify_session_bookings(
            session, f'Class is starting: {session.title}',
            f'{session.title} is now ongoing. Open your booking to join the class.',
        )
        messages.success(request, 'Class started. Confirmed students can now join.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
@require_POST
def session_end(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        if session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ):
            services.stop_livekit_recording(session)
        services.end_session(session)
        messages.success(request, 'Class ended and marked as completed.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@verified_teacher_required
@require_POST
def session_cancel(request, session_id):
    session = get_object_or_404(TeachingSession, pk=session_id, teacher=request.user)
    try:
        if session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ):
            services.stop_livekit_recording(session)
        services.notify_session_bookings(
            session, f'Session cancelled: {session.title}',
            f'{session.title} scheduled for {timezone.localtime(session.starts_at):%Y-%m-%d %H:%M} has been cancelled.',
        )
        services.cancel_session(session)
        messages.success(request, 'Session cancelled; confirmed students were notified by email where configured.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@login_required
@require_POST
def session_book(request, session_id):
    _student(request)
    s = get_object_or_404(TeachingSession, pk=session_id, teacher__teacher_profile__verification_status='approved')
    try:
        b = services.book_session(request.user, s)
        b.session = s
        b.student = request.user
        services.notify_booking(b)
        messages.success(request, 'Booked.' if b.status == SessionBooking.Status.CONFIRMED else
                         'Booking held. It is confirmed once your payment is verified by an administrator.')
        return redirect('market:session_detail', session_id=s.pk)
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=s.pk)


@login_required
@require_POST
def session_booking_cancel(request, session_id):
    _student(request)
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        services.cancel_session_booking(request.user, session)
        services.notify_booking_cancelled(request.user, session)
        messages.success(request, 'Your booking was cancelled.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:session_detail', session_id=session.pk)


@login_required
def session_join(request, session_id):
    _student(request)
    session = get_object_or_404(TeachingSession, pk=session_id)
    try:
        _classroom_role(request, session)
    except MarketError as exc:
        messages.error(request, str(exc))
        return redirect('market:session_detail', session_id=session.pk)
    return redirect('market:session_classroom', session_id=session.pk)


# ---------------- reviews ----------------

@login_required
@require_POST
def review_create(request, teacher_id):
    _student(request)
    t = _approved_teacher_or_404(teacher_id)
    if not services.can_review(request.user, t.user):
        messages.error(request, 'You can review a teacher after joining their class or attending a session.')
        return redirect('teachers:public_profile', teacher_id=t.id)
    form = ReviewForm(request.POST)
    if form.is_valid():
        Review.objects.update_or_create(teacher=t.user, student=request.user, defaults={
            'rating': form.cleaned_data['rating'], 'comment': form.cleaned_data['comment'],
            'status': Review.Status.PENDING})
        messages.success(request, 'Thanks! Your review will appear after moderation.')
    return redirect('teachers:public_profile', teacher_id=t.id)


# ---------------- admin: payments + review moderation ----------------

@admin_required
def admin_queue(request):
    return render(request, 'marketplace/admin_queue.html', {
        'payments': Payment.objects.filter(status=Payment.Status.PENDING).select_related('student', 'teacher', 'resource', 'session'),
        'reviews': Review.objects.filter(status=Review.Status.PENDING).select_related('student', 'teacher')})


@admin_required
@require_POST
def admin_payment(request, payment_id):
    try:
        services.decide_payment(request.user, payment_id, request.POST.get('decision') == 'confirm')
        messages.success(request, 'Payment updated.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:admin_queue')


@admin_required
@require_POST
def admin_review(request, review_id):
    r = get_object_or_404(Review, pk=review_id)
    r.status = Review.Status.APPROVED if request.POST.get('decision') == 'approve' else Review.Status.REJECTED
    r.save(update_fields=['status'])
    return redirect('market:admin_queue')
