from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from apps.analytics import performance, weak_topics, recommendations
from apps.quizzes.models import Quiz


@login_required
def home(request):
    if request.user.is_admin_role:
        return redirect('admin_panel:dashboard')
    if request.user.is_teacher:
        return redirect('teachers:dashboard')

    overview = performance.student_overview(request.user)
    context = {
        'overview': overview,
        'courses': performance.course_wise_performance(request.user),
        'weak': weak_topics.strongest_and_weakest(request.user),
        'recommendations': recommendations.recommendations_for_student(request.user, use_ai=False)[:3],
        'recent_quizzes': Quiz.objects.filter(student=request.user, completed=True).select_related('topic')[:5],
    }
    return render(request, 'student/dashboard.html', context)
