"""
Custom admin panel.

Note: since courses/topics are now student-owned (per the FYP's
no-hardcoded-content requirement), the admin no longer creates or edits
subjects/topics/questions directly — that content lives entirely under each
student's own account. The admin's role here is oversight: platform-wide
stats and a read-only view into any student's courses/performance.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, IntegerField, OuterRef, Q, Subquery, Value, Window
from django.db.models.functions import Coalesce
from django.db.models.functions.window import RowNumber
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .permissions import admin_required
from apps.classrooms.models import Enrollment
from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz
from apps.teachers.models import TeacherProfile
from apps.marketplace.models import Payment, Review, TeachingSession
from apps.analytics import performance, weak_topics

User = get_user_model()


@admin_required
def admin_dashboard(request):
    now = timezone.now()
    recent_cutoff = now - timedelta(days=30)

    session_counts = dict(
        TeachingSession.objects.values('status').annotate(total=Count('pk')).values_list('status', 'total')
    )
    recording_counts = dict(
        TeachingSession.objects.values('recording_status').annotate(total=Count('pk'))
        .values_list('recording_status', 'total')
    )

    total_students = User.objects.filter(role=User.Role.STUDENT).count()
    total_teachers = User.objects.filter(role=User.Role.TEACHER).count()
    pending_teachers = TeacherProfile.objects.filter(
        verification_status=TeacherProfile.Status.PENDING,
    ).count()
    pending_payments = Payment.objects.filter(status=Payment.Status.PENDING).count()
    pending_reviews = Review.objects.filter(status=Review.Status.PENDING).count()
    total_enrollments = Enrollment.objects.count()
    completed_recently = TeachingSession.objects.filter(
        status=TeachingSession.Status.COMPLETED,
        starts_at__gte=recent_cutoff,
        starts_at__lte=now,
    ).count()
    upcoming_sessions = TeachingSession.objects.filter(
        status=TeachingSession.Status.UPCOMING,
        starts_at__gt=now,
    ).count()

    activity = []
    recent_users = User.objects.filter(
        role__in=(User.Role.STUDENT, User.Role.TEACHER),
    ).order_by('-created_at').values(
        'role', 'first_name', 'last_name', 'username', 'created_at',
    )[:8]
    for user in recent_users:
        display_name = ' '.join(part for part in (user['first_name'], user['last_name']) if part)
        activity.append({
            'at': user['created_at'],
            'description': f"New {'student' if user['role'] == User.Role.STUDENT else 'teacher'} registration: {display_name or user['username']}",
            'kind': 'Registration',
        })

    approvals = TeacherProfile.objects.filter(
        verified_by__isnull=False,
        verified_at__isnull=False,
    ).select_related('user').order_by('-verified_at')[:8]
    for profile in approvals:
        activity.append({
            'at': profile.verified_at,
            'description': f'Teacher approved: {profile.user.get_full_name() or profile.user.username}',
            'kind': 'Teacher approval',
        })

    for session in TeachingSession.objects.select_related('teacher').order_by('-created_at')[:8]:
        activity.append({
            'at': session.created_at,
            'description': f'Online session scheduled: {session.title} · {session.teacher.get_full_name() or session.teacher.username}',
            'kind': 'Online session',
        })

    for payment in Payment.objects.select_related('student').order_by('-created_at')[:8]:
        activity.append({
            'at': payment.confirmed_at or payment.created_at,
            'description': f'Payment record (current status: {payment.get_status_display().lower()}): {payment.student.get_full_name() or payment.student.username}',
            'kind': 'Payment',
        })

    for review in Review.objects.select_related('student', 'teacher').order_by('-created_at')[:8]:
        activity.append({
            'at': review.created_at,
            'description': f'Review record (current status: {review.get_status_display().lower()}): {review.student.get_full_name() or review.student.username} → {review.teacher.get_full_name() or review.teacher.username}',
            'kind': 'Review',
        })
    activity.sort(key=lambda item: item['at'], reverse=True)

    context = {
        'platform_cards': [
            {'icon': '♙', 'title': 'Total Students', 'value': total_students, 'description': 'Registered student accounts', 'url': reverse('admin_panel:students'), 'link': 'View students'},
            {'icon': '♟', 'title': 'Total Teachers', 'value': total_teachers, 'description': 'Teacher accounts', 'url': reverse('admin_panel:teachers'), 'link': 'View teachers'},
            {'icon': '✓', 'title': 'Total Quizzes', 'value': Quiz.objects.count(), 'description': 'Student-owned quizzes'},
            {'icon': '▣', 'title': 'Total Online Sessions', 'value': sum(session_counts.values()), 'description': 'Scheduled classroom sessions'},
            {'icon': '⇄', 'title': 'Total Enrollments', 'value': total_enrollments, 'description': 'Classroom enrollment records'},
        ],
        'pending_cards': [
            {'icon': '♟', 'title': 'Teachers awaiting approval', 'value': pending_teachers, 'url': reverse('admin_panel:teachers'), 'link': 'View details'},
            {'icon': '¤', 'title': 'Pending payment verification', 'value': pending_payments, 'url': reverse('market:admin_queue'), 'link': 'View details'},
            {'icon': '☆', 'title': 'Pending reviews', 'value': pending_reviews, 'url': reverse('market:admin_queue'), 'link': 'View details'},
        ],
        'classroom_cards': [
            {'icon': '●', 'title': 'Active Sessions', 'value': session_counts.get(TeachingSession.Status.ONGOING, 0), 'description': 'Currently ongoing'},
            {'icon': '✓', 'title': 'Recently Completed', 'value': completed_recently, 'description': 'Completed in the last 30 days'},
            {'icon': '◷', 'title': 'Upcoming Sessions', 'value': upcoming_sessions, 'description': 'Scheduled for a future time'},
            {'icon': '◌', 'title': 'Recording Processing', 'value': sum(recording_counts.get(status, 0) for status in (
                TeachingSession.RecordingStatus.STARTING,
                TeachingSession.RecordingStatus.RECORDING,
                TeachingSession.RecordingStatus.STOPPING,
            )), 'description': 'Starting, recording or stopping'},
            {'icon': '▶', 'title': 'Recordings Completed', 'value': recording_counts.get(TeachingSession.RecordingStatus.COMPLETED, 0), 'description': 'Private recordings ready'},
            {'icon': '!', 'title': 'Recordings Failed', 'value': recording_counts.get(TeachingSession.RecordingStatus.FAILED, 0), 'description': 'Recording jobs marked failed'},
        ],
        'recent_activity': activity[:10],
    }
    return render(request, 'admin/dashboard.html', context)


@admin_required
def students_list(request):
    now = timezone.now()
    active_since = now - timedelta(days=30)
    student_filter = {'role': User.Role.STUDENT}

    ahead_students = User.objects.filter(role=User.Role.STUDENT).filter(
        Q(xp_points__gt=OuterRef('xp_points'))
        | Q(xp_points=OuterRef('xp_points'), username__lt=OuterRef('username'))
        | Q(
            xp_points=OuterRef('xp_points'),
            username=OuterRef('username'),
            pk__lt=OuterRef('pk'),
        )
    ).order_by().values('role').annotate(ahead_count=Count('pk')).values('ahead_count')[:1]

    students = User.objects.filter(**student_filter).annotate(
        rank=Coalesce(
            Subquery(ahead_students, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ) + Value(1),
        quiz_attempts=Count('quizzes', filter=Q(quizzes__completed=True), distinct=True),
        average_quiz_score=Avg('quizzes__percentage', filter=Q(quizzes__completed=True)),
        course_count=Count('courses', distinct=True),
    )

    total_students = User.objects.filter(**student_filter).count()
    active_students = User.objects.filter(**student_filter, last_login__gte=active_since).count()

    top_students = list(students.order_by('-xp_points', 'username', 'pk')[:3])
    top_student = top_students[0] if top_students else None

    query = request.GET.get('q', '').strip()[:100]
    rank_filter = request.GET.get('rank', 'all')
    sort_order = request.GET.get('sort', 'highest')
    joined_filter = request.GET.get('joined', 'all')
    activity_filter = request.GET.get('activity', 'all')

    if query:
        for term in query.split():
            students = students.filter(
                Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(username__icontains=term)
                | Q(email__icontains=term)
            )

    rank_limits = {'top_3': 3, 'top_10': 10, 'top_25': 25}
    if rank_filter in rank_limits:
        students = students.filter(rank__lte=rank_limits[rank_filter])
    else:
        rank_filter = 'all'

    joined_days = {'30': 30, '90': 90, '365': 365}
    if joined_filter in joined_days:
        students = students.filter(created_at__gte=now - timedelta(days=joined_days[joined_filter]))
    else:
        joined_filter = 'all'

    if activity_filter == 'active':
        students = students.filter(last_login__gte=active_since)
    elif activity_filter == 'inactive':
        students = students.filter(Q(last_login__lt=active_since) | Q(last_login__isnull=True))
    else:
        activity_filter = 'all'

    if sort_order == 'lowest':
        students = students.order_by('xp_points', 'username', 'pk')
    else:
        sort_order = 'highest'
        students = students.order_by('-xp_points', 'username', 'pk')

    paginator = Paginator(students, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'admin/students.html', {
        'students': page_obj.object_list,
        'page_obj': page_obj,
        'query': query,
        'rank_filter': rank_filter,
        'sort_order': sort_order,
        'joined_filter': joined_filter,
        'activity_filter': activity_filter,
        'rank_filters': (
            ('all', 'All ranks'), ('top_3', 'Top 3'),
            ('top_10', 'Top 10'), ('top_25', 'Top 25'),
        ),
        'total_students': total_students,
        'top_student': top_student,
        'top_students': top_students,
        'active_students': active_students,
        'active_since': active_since,
    })


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
    now = timezone.now()
    active_since = now - timedelta(days=30)
    approved_reviews = Q(user__reviews_received__status=Review.Status.APPROVED)
    approved_sessions = Q(user__teaching_sessions__status=TeachingSession.Status.COMPLETED)
    base = TeacherProfile.objects.select_related('user').annotate(
        review_count=Count('user__reviews_received', filter=approved_reviews, distinct=True),
        average_rating=Avg('user__reviews_received__rating', filter=approved_reviews),
        completed_sessions=Count('user__teaching_sessions', filter=approved_sessions, distinct=True),
    )

    total_teachers = User.objects.filter(role=User.Role.TEACHER).count()
    status_counts = dict(TeacherProfile.objects.values('verification_status').annotate(
        total=Count('pk'),
    ).values_list('verification_status', 'total'))
    global_rating = Review.objects.filter(
        status=Review.Status.APPROVED, teacher__role=User.Role.TEACHER,
    ).aggregate(value=Avg('rating'))['value'] or 0
    active_teachers = User.objects.filter(role=User.Role.TEACHER, last_login__gte=active_since).count()

    # A Bayesian weighted rating rewards consistent evidence while shrinking small
    # review samples toward the platform-wide approved-review average.
    leaderboard = []
    for profile in base.filter(verification_status=TeacherProfile.Status.APPROVED):
        if not profile.review_count:
            continue
        profile.skills = profile.subject_list
        profile.verified_skill_count = len(profile.skills)
        profile.ranking_score = (
            profile.review_count * float(profile.average_rating or 0) + 5 * float(global_rating)
        ) / (profile.review_count + 5)
        leaderboard.append(profile)
    leaderboard.sort(key=lambda p: (
        -p.ranking_score, -p.review_count, -p.verified_skill_count,
        -float(p.average_rating or 0), p.user.username.casefold(), p.pk,
    ))
    for rank, profile in enumerate(leaderboard, 1):
        profile.rank = rank
    leaderboard = leaderboard[:10]

    status = request.GET.get('status', TeacherProfile.Status.PENDING)
    if status not in TeacherProfile.Status.values:
        status = 'all' if status == 'all' else TeacherProfile.Status.PENDING
    query = request.GET.get('q', '').strip()[:100]
    mode = request.GET.get('mode', '').strip()
    qs = base
    if status != 'all':
        qs = qs.filter(verification_status=status)
    if query:
        for term in query.split():
            qs = qs.filter(
                Q(user__first_name__icontains=term) | Q(user__last_name__icontains=term)
                | Q(user__username__icontains=term) | Q(user__email__icontains=term)
                | Q(subjects__icontains=term) | Q(qualifications__icontains=term)
            )
    if mode in TeacherProfile.Mode.values:
        qs = qs.filter(teaching_mode=mode)
    else:
        mode = ''
    qs = qs.order_by('user__last_name', 'user__first_name', 'user__username', 'pk')
    page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))

    page_profiles = list(page_obj.object_list)
    reviews_by_teacher = {}
    if page_profiles:
        teacher_ids = [profile.user_id for profile in page_profiles]
        for review in Review.objects.filter(
            teacher_id__in=teacher_ids, status=Review.Status.APPROVED,
        ).annotate(
            teacher_review_rank=Window(
                expression=RowNumber(), partition_by=[F('teacher_id')],
                order_by=F('created_at').desc(),
            ),
        ).filter(teacher_review_rank__lte=2).select_related('student').order_by('teacher_id', '-created_at'):
            reviews_by_teacher.setdefault(review.teacher_id, [])
            reviews_by_teacher[review.teacher_id].append(review)
    for profile in page_profiles:
        profile.skills = profile.subject_list
        profile.recent_reviews = reviews_by_teacher.get(profile.user_id, [])

    return render(request, 'admin/teachers.html', {
        'profiles': page_profiles, 'page_obj': page_obj, 'status': status,
        'statuses': (('all', 'All teachers'), *TeacherProfile.Status.choices),
        'query': query, 'mode': mode, 'modes': TeacherProfile.Mode.choices,
        'leaderboard': leaderboard, 'global_rating': global_rating,
        'total_teachers': total_teachers,
        'pending_count': status_counts.get(TeacherProfile.Status.PENDING, 0),
        'approved_count': status_counts.get(TeacherProfile.Status.APPROVED, 0),
        'average_rating': global_rating, 'active_teachers': active_teachers,
    })


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
