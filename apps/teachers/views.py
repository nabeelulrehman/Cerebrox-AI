from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import User
from apps.accounts.permissions import teacher_required, verified_teacher_required
from .forms import TeacherRegisterForm, TeacherProfileForm
from .models import TeacherProfile, TeacherPaymentMethod


def teacher_register(request):
    if request.user.is_authenticated:
        return redirect('teachers:dashboard' if request.user.is_teacher else 'dashboard:home')
    form = TeacherRegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Account created. An administrator must verify your profile before you can use teacher tools.')
        return redirect('teachers:dashboard')
    return render(request, 'teachers/register.html', {'form': form})


@teacher_required
def dashboard(request):
    from apps.classrooms.models import Classroom, Enrollment
    profile = TeacherProfile.objects.filter(user=request.user).first()
    if profile is None:
        messages.error(
            request,
            'Your teacher profile is missing. Please contact an administrator to restore it.',
        )
        return render(request, 'teacher/dashboard.html', {'profile': None}, status=409)
    ctx = {'profile': profile}
    from apps.marketplace.models import Payment
    ctx['recent_session_payments'] = Payment.objects.filter(
        teacher=request.user,
    ).select_related(
        'student', 'session', 'resource', 'classroom')[:5]
    ctx['pending_teacher_payments'] = Payment.objects.filter(
        teacher=request.user, is_manual_teacher_payment=True, status=Payment.Status.PENDING,
    ).select_related('student', 'resource', 'session', 'classroom', 'teacher_payment_method').order_by('-created_at')
    if profile.is_approved:
        classes = Classroom.objects.filter(teacher=request.user, is_active=True)
        ctx.update({
            'classes': classes,
            'pending_requests': Enrollment.objects.filter(
                classroom__teacher=request.user, status=Enrollment.Status.REQUESTED).count(),
            'student_count': Enrollment.objects.filter(
                classroom__teacher=request.user, status=Enrollment.Status.ACTIVE).count(),
        })
    return render(request, 'teacher/dashboard.html', ctx)


@teacher_required
def profile_edit(request):
    # Reuse the authenticated user's reverse one-to-one cache when the menu has
    # already loaded it; otherwise this remains a single profile lookup.
    profile = getattr(request.user, 'teacher_profile', None)
    if profile is None:
        messages.error(
            request,
            'Your teacher profile is missing. Please contact an administrator to restore it.',
        )
        return redirect('teachers:dashboard')
    payment_method = TeacherPaymentMethod.objects.filter(teacher=request.user, is_active=True).order_by('-updated_at').first()
    form = TeacherProfileForm(request.POST or None, instance=profile, payment_method=payment_method)
    if request.method == 'POST':
        if form.is_valid():
            form.save()
            form.save_payment_method(request.user)
            messages.success(request, 'Teacher profile updated.')
            return redirect('teachers:profile_edit')
    template = 'teacher/profile_edit_fragment.html' if request.headers.get('x-requested-with') == 'XMLHttpRequest' else 'teacher/profile_edit.html'
    return render(request, template, {'form': form, 'profile': profile})


@verified_teacher_required
def payment_decide(request, payment_id):
    from django.views.decorators.http import require_POST
    from apps.marketplace.models import Payment
    from apps.marketplace.services import decide_teacher_payment, MarketError
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    approve = request.POST.get('decision') == 'approve'
    try:
        payment = decide_teacher_payment(request.user, payment_id, approve=approve,
                                         reason=request.POST.get('reason', ''))
        from django.core.mail import send_mail
        outcome = 'approved' if approve else 'rejected'
        if payment.student.email:
            send_mail(f'Payment {outcome}',
                      f'Your payment {payment.reference} was {outcome} by your teacher.' +
                      (f' Reason: {payment.review_note}' if payment.review_note else ''),
                      None, [payment.student.email], fail_silently=True)
        messages.success(request, f'Payment {outcome}.')
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect('teachers:dashboard')


# ---------- Public directory (verified teachers only) ----------

def directory(request):
    qs = TeacherProfile.objects.filter(
        verification_status=TeacherProfile.Status.APPROVED).select_related('user')
    q = request.GET.get('q', '').strip()
    mode = request.GET.get('mode', '')
    free = request.GET.get('free') == '1'
    max_price = request.GET.get('max_price', '').strip()
    min_exp = request.GET.get('min_exp', '').strip()
    if q:
        qs = qs.filter(Q(subjects__icontains=q) | Q(qualifications__icontains=q) |
                       Q(headline__icontains=q) | Q(user__first_name__icontains=q) |
                       Q(user__last_name__icontains=q))
    if mode in TeacherProfile.Mode.values:
        qs = qs.filter(Q(teaching_mode=mode) | Q(teaching_mode=TeacherProfile.Mode.BOTH))
    if free:
        qs = qs.filter(offers_free_guidance=True)
    if max_price.replace('.', '', 1).isdigit():
        qs = qs.filter(Q(hourly_rate__lte=max_price) | Q(hourly_rate__isnull=True))
    if min_exp.isdigit():
        qs = qs.filter(experience_years__gte=int(min_exp))
    return render(request, 'teachers/directory.html', {'teachers': qs, 'q': q, 'mode': mode, 'free': free,
                                                       'max_price': max_price, 'min_exp': min_exp,
                                                       'modes': TeacherProfile.Mode.choices})


def teacher_public_profile(request, teacher_id):
    # Only approved teachers are ever publicly visible. Contact details (email) are not exposed.
    profile = get_object_or_404(TeacherProfile.objects.select_related('user'), pk=teacher_id,
                                verification_status=TeacherProfile.Status.APPROVED)
    from apps.classrooms.models import Classroom
    classes = Classroom.objects.filter(teacher=profile.user, is_active=True, listed_publicly=True)
    from django.db.models import Avg
    from apps.marketplace.models import Review
    from apps.marketplace.services import can_review
    reviews = Review.objects.filter(teacher=profile.user, status=Review.Status.APPROVED).select_related('student')
    return render(request, 'teachers/public_profile.html', {
        'profile': profile, 'classes': classes, 'reviews': reviews[:20],
        'avg_rating': reviews.aggregate(a=Avg('rating'))['a'],
        'can_review': request.user.is_authenticated and can_review(request.user, profile.user),
        'is_student': request.user.is_authenticated and request.user.is_student})
