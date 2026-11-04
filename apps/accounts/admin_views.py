"""
Custom admin panel.

Note: since courses/topics are now student-owned (per the FYP's
no-hardcoded-content requirement), the admin no longer creates or edits
subjects/topics/questions directly — that content lives entirely under each
student's own account. The admin's role here is oversight: platform-wide
stats and a read-only view into any student's courses/performance.
"""
from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .permissions import admin_required
from django.contrib.auth import get_user_model
from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz
from apps.analytics import performance, weak_topics

User = get_user_model()


@admin_required
def admin_dashboard(request):
    context = {
        'total_students': User.objects.filter(role=User.Role.STUDENT).count(),
        'total_courses': Course.objects.count(),
        'total_topics': Topic.objects.count(),
        'total_quizzes': Quiz.objects.count(),
    }
    return render(request, 'admin/dashboard.html', context)


@admin_required
def students_list(request):
    students = User.objects.filter(role=User.Role.STUDENT).order_by('-created_at')
    return render(request, 'admin/students.html', {'students': students})


@admin_required
def student_detail(request, student_id):
    student = get_object_or_404(User, pk=student_id, role=User.Role.STUDENT)
    context = {
        'student': student,
        'courses': Course.objects.filter(student=student),
        'overview': performance.student_overview(student),
        'weak': weak_topics.strongest_and_weakest(student),
    }
    return render(request, 'admin/student_detail.html', context)


@admin_required
def teachers_list(request):
    from apps.teachers.models import TeacherProfile
    status = request.GET.get('status', 'pending')
    qs = TeacherProfile.objects.select_related('user')
    if status in TeacherProfile.Status.values:
        qs = qs.filter(verification_status=status)
    return render(request, 'admin/teachers.html', {'profiles': qs, 'status': status,
                                                   'statuses': TeacherProfile.Status.choices})


@admin_required
@require_POST
def teacher_decide(request, profile_id):
    from apps.teachers.models import TeacherProfile
    profile = get_object_or_404(TeacherProfile, pk=profile_id)
    decision = request.POST.get('decision')
    mapping = {'approve': TeacherProfile.Status.APPROVED, 'reject': TeacherProfile.Status.REJECTED,
               'suspend': TeacherProfile.Status.SUSPENDED}
    if decision not in mapping:
        messages.error(request, 'Unknown decision.')
    else:
        profile.set_status(mapping[decision], request.user, request.POST.get('note', ''))
        messages.success(request, f'{profile.user} is now {profile.get_verification_status_display().lower()}.')
    return redirect('admin_panel:teachers')
