from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import User
from apps.accounts.permissions import teacher_required, verified_teacher_required
from .forms import TeacherRegisterForm, TeacherProfileForm
from .models import TeacherProfile


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
    profile = TeacherProfile.objects.filter(user=request.user).first()
    if profile is None:
        messages.error(
            request,
            'Your teacher profile is missing. Please contact an administrator to restore it.',
        )
        return redirect('teachers:dashboard')
    form = TeacherProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Teacher profile updated.')
        return redirect('teachers:dashboard')
    return render(request, 'teacher/profile_edit.html', {'form': form, 'profile': profile})


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
