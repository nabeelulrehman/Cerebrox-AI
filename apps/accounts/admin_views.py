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
from django.db.models import Avg, Count, F, IntegerField, OuterRef, Q, Subquery, Value, Window, Sum
from django.db.models.functions import Coalesce
from django.db.models.functions.window import RowNumber
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .permissions import admin_required
from apps.classrooms.models import Enrollment
from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz
from apps.teachers.models import TeacherProfile, TeacherPaymentMethod
from apps.marketplace.models import (
    Payment, PaymentMethodConfiguration, Review, TeachingSession, CommissionSettings,
)
from apps.analytics import performance, weak_topics

User = get_user_model()


@admin_required
def payment_reports(request):
    if request.method == 'POST' and request.POST.get('section') == 'platform_payment_settings':
        config, _ = PaymentMethodConfiguration.objects.get_or_create(pk=1)
        selected_method = request.POST.get('payment_method')
        if selected_method == 'jazzcash':
            config.jazzcash_enabled = True
            config.jazzcash_account_title = request.POST.get('jazzcash_account_title', '').strip()[:160]
            config.jazzcash_account_number = request.POST.get('jazzcash_account_number', '').strip()[:40]
        elif selected_method == 'easypaisa':
            config.easypaisa_enabled = True
            config.easypaisa_account_title = request.POST.get('easypaisa_account_title', '').strip()[:160]
            config.easypaisa_account_number = request.POST.get('easypaisa_account_number', '').strip()[:40]
        elif selected_method == 'bank':
            config.bank_transfer_enabled = True
            config.bank_account_title = request.POST.get('bank_account_title', '').strip()[:160]
            config.bank_name = request.POST.get('bank_name', '').strip()[:160]
            config.bank_iban = request.POST.get('bank_iban', '').strip()[:64]
            config.bank_raast_id = request.POST.get('bank_raast_id', '').strip()[:100]
            config.bank_instructions = request.POST.get('bank_instructions', '').strip()
        config.save()
        messages.success(request, 'Platform payment settings updated.')
        return redirect(f"{reverse('admin_panel:payment_reports')}#platform-settings")

    payments = Payment.objects.select_related(
        'student', 'teacher', 'resource', 'session', 'classroom',
    ).prefetch_related('status_audits__actor').all()
    status = request.GET.get('status', '')
    teacher_id = request.GET.get('teacher', '')
    student_id = request.GET.get('student', '')
    date_from = request.GET.get('from', '')
    date_to = request.GET.get('to', '')
    if status in Payment.Status.values:
        payments = payments.filter(status=status)
    if teacher_id.isdigit():
        payments = payments.filter(teacher_id=teacher_id)
    if student_id.isdigit():
        payments = payments.filter(student_id=student_id)
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        payments = payments.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        payments = payments.filter(created_at__date__lte=parsed_to)
    payment_page_obj = Paginator(payments, 50).get_page(request.GET.get('page'))
    all_payments = Payment.objects.all()
    sales_payments = all_payments.filter(is_platform_payment=False)
    from apps.marketplace.services import teacher_financial_summary
    financial_statements = [
        {'teacher': teacher, **teacher_financial_summary(teacher)}
        for teacher in User.objects.filter(role=User.Role.TEACHER).order_by('username')
    ]
    from apps.notifications.models import Notification
    return render(request, 'admin/payment_reports.html', {
        'payment_notifications': Notification.objects.filter(
            recipient=request.user, notification_type=Notification.Type.PAYMENT,
        )[:4],
        'payments': payment_page_obj.object_list, 'payment_page_obj': payment_page_obj,
        'status': status, 'teacher_id': teacher_id, 'student_id': student_id,
        'pending_verifications': Payment.objects.filter(status=Payment.Status.PENDING).select_related(
            'student', 'teacher', 'resource', 'session', 'classroom',
        ).order_by('-created_at'),
        'teacher_payment_methods': TeacherPaymentMethod.objects.select_related('teacher').order_by(
            'teacher__username', 'method', 'account_title',
        ),
        'payment_config': PaymentMethodConfiguration.objects.filter(pk=1).first(),
        'reviews': Review.objects.filter(status=Review.Status.PENDING).select_related(
            'student', 'teacher',
        ).order_by('-pk'),
        'status_choices': Payment.Status.choices,
        'date_from': date_from, 'date_to': date_to,
        'teachers': User.objects.filter(role=User.Role.TEACHER).order_by('username'),
        'students': User.objects.filter(role=User.Role.STUDENT).order_by('username'),
        'gross': sales_payments.filter(status=Payment.Status.CONFIRMED).aggregate(v=Sum('amount'))['v'] or 0,
        'commission': sales_payments.filter(status=Payment.Status.CONFIRMED).aggregate(v=Sum('platform_commission'))['v'] or 0,
        'earnings': sales_payments.filter(status=Payment.Status.CONFIRMED).aggregate(v=Sum('teacher_earning'))['v'] or 0,
        'pending_count': all_payments.filter(status=Payment.Status.PENDING).count(),
        'approved_count': all_payments.filter(status=Payment.Status.CONFIRMED).count(),
        'rejected_count': all_payments.filter(status=Payment.Status.REJECTED).count(),
        'commission_rate': CommissionSettings.objects.filter(pk=1).first(),
        'pending_platform_payments': Payment.objects.filter(is_platform_payment=True,
            status=Payment.Status.PENDING).select_related('teacher').order_by('-created_at'),
        'financial_statements': financial_statements,
    })


@admin_required
@require_POST
def commission_update(request):
    from decimal import Decimal, InvalidOperation
    try:
        value = Decimal(request.POST.get('percentage', '').strip())
        if not value.is_finite() or value < 0 or value > 100 or value.as_tuple().exponent < -2:
            raise InvalidOperation
        settings_obj, _ = CommissionSettings.objects.get_or_create(pk=1)
        settings_obj.percentage = value
        settings_obj.updated_by = request.user
        settings_obj.save(update_fields=['percentage', 'updated_by', 'updated_at'])
        messages.success(request, 'Commission rate updated. Existing payments retain their recorded rate.')
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter a commission percentage between 0 and 100, with up to 2 decimal places.')
    return redirect(f"{reverse('admin_panel:payment_reports')}#commission")


@admin_required
def admin_dashboard(request):
    from apps.marketplace.services import teacher_financial_summary
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
    finance_summary = Payment.objects.filter(is_platform_payment=False, status=Payment.Status.CONFIRMED).aggregate(
        commission=Sum('platform_commission'), teacher_earnings=Sum('teacher_earning'))
    outstanding_platform_dues = sum((teacher_financial_summary(t)['outstanding_platform']
        for t in User.objects.filter(role=User.Role.TEACHER)), 0)
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
            {'icon': '¤', 'title': 'Platform Commission', 'value': finance_summary['commission'] or 0, 'description': 'Commission on confirmed transactions', 'url': reverse('admin_panel:payment_reports'), 'link': 'Financial management'},
            {'icon': '¤', 'title': 'Outstanding Platform Dues', 'value': outstanding_platform_dues, 'description': 'Teacher commissions not yet remitted', 'url': reverse('admin_panel:payment_reports'), 'link': 'Review payments'},
        ],
        'pending_cards': [
            {'icon': '♟', 'title': 'Teachers awaiting approval', 'value': pending_teachers, 'url': reverse('admin_panel:teachers'), 'link': 'View details'},
            {'icon': '¤', 'title': 'Pending payment verification', 'value': pending_payments, 'url': reverse('admin_panel:payment_reports') + '#transactions', 'link': 'View details'},
            {'icon': '☆', 'title': 'Pending reviews', 'value': pending_reviews, 'url': reverse('admin_panel:payment_reports') + '#reviews', 'link': 'View details'},
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
