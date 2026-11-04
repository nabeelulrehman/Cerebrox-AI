import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import admin_required, verified_teacher_required
from apps.teachers.models import TeacherProfile
from . import services
from .forms import ResourceForm, GuidanceForm, SessionForm, ReviewForm
from .models import Resource, Payment, GuidanceRequest, TeachingSession, SessionBooking, Review
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
        g.student, g.teacher = request.user, t.user
        g.save()
        messages.success(request, 'Your message was sent. The teacher can reply here in the app.')
    return redirect('teachers:public_profile', teacher_id=t.id)


@verified_teacher_required
def teacher_inbox(request):
    return render(request, 'marketplace/inbox.html', {'items': GuidanceRequest.objects.filter(teacher=request.user)
                                                      .select_related('student')})


@verified_teacher_required
@require_POST
def guidance_reply(request, request_id):
    g = get_object_or_404(GuidanceRequest, pk=request_id, teacher=request.user)
    g.reply, g.status = request.POST.get('reply', '')[:2000], GuidanceRequest.Status.ANSWERED
    g.save(update_fields=['reply', 'status'])
    return redirect('market:teacher_inbox')


@login_required
def my_guidance(request):
    _student(request)
    return render(request, 'marketplace/my_guidance.html',
                  {'items': GuidanceRequest.objects.filter(student=request.user).select_related('teacher')})


# ---------------- sessions ----------------

@verified_teacher_required
def teacher_sessions(request):
    form = SessionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        s = form.save(commit=False)
        s.teacher = request.user
        s.save()
        messages.success(request, 'Session created.')
        return redirect('market:teacher_sessions')
    return render(request, 'marketplace/teacher_sessions.html',
                  {'form': form, 'sessions': TeachingSession.objects.filter(teacher=request.user)})


@login_required
def sessions_list(request):
    _student(request)
    from django.utils import timezone
    upcoming = TeachingSession.objects.filter(
        starts_at__gt=timezone.now(), teacher__teacher_profile__verification_status='approved')
    mine = {b.session_id: b for b in SessionBooking.objects.filter(student=request.user)}
    return render(request, 'marketplace/sessions.html', {'sessions': upcoming, 'mine': mine})


@login_required
@require_POST
def session_book(request, session_id):
    _student(request)
    s = get_object_or_404(TeachingSession, pk=session_id, teacher__teacher_profile__verification_status='approved')
    try:
        b = services.book_session(request.user, s)
        messages.success(request, 'Booked.' if b.status == SessionBooking.Status.CONFIRMED else
                         'Booking held. It is confirmed once your payment is verified by an administrator.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('market:sessions')


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
