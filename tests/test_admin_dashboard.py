from decimal import Decimal
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.classrooms.models import Classroom, Enrollment
from apps.marketplace.models import Payment, Review, TeachingSession
from apps.quizzes.models import Quiz
from apps.teachers.models import TeacherProfile
from apps.learning.models import Course, Topic

User = get_user_model()


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='dashboard-admin', password='StrongPass123', role=User.Role.ADMIN,
        )
        self.student = User.objects.create_user(
            username='dashboard-student', password='StrongPass123', role=User.Role.STUDENT,
        )
        self.teacher = User.objects.create_user(
            username='dashboard-teacher', password='StrongPass123', role=User.Role.TEACHER,
        )
        self.teacher_profile = TeacherProfile.objects.create(
            user=self.teacher, qualifications='MSc', subjects='Physics',
        )
        self.course = Course.objects.create(student=self.student, name='Physics', icon='P')
        self.topic = Topic.objects.create(course=self.course, name='Motion')
        self.quiz = Quiz.objects.create(student=self.student, topic=self.topic)
        self.classroom = Classroom.objects.create(teacher=self.teacher, name='Physics class')
        self.enrollment = Enrollment.objects.create(
            classroom=self.classroom, student=self.student, status=Enrollment.Status.ACTIVE,
        )
        self.client.force_login(self.admin)

    def _session(self, title, status, recording_status=TeachingSession.RecordingStatus.IDLE, starts_at=None):
        return TeachingSession.objects.create(
            teacher=self.teacher,
            title=title,
            starts_at=starts_at or timezone.now() + timedelta(days=1),
            duration_minutes=60,
            status=status,
            recording_status=recording_status,
        )

    def test_dashboard_cards_use_existing_platform_records(self):
        now = timezone.now()
        self._session('Live class', TeachingSession.Status.ONGOING, TeachingSession.RecordingStatus.RECORDING,
                      starts_at=now - timedelta(minutes=5))
        self._session('Finished class', TeachingSession.Status.COMPLETED, TeachingSession.RecordingStatus.COMPLETED,
                      starts_at=now - timedelta(days=2))
        self._session('Future class', TeachingSession.Status.UPCOMING,
                      starts_at=now + timedelta(days=2))
        self._session('Failed recording', TeachingSession.Status.COMPLETED,
                      TeachingSession.RecordingStatus.FAILED, starts_at=now - timedelta(days=1))
        Payment.objects.create(
            student=self.student, teacher=self.teacher, session=TeachingSession.objects.first(),
            amount=Decimal('25.00'), status=Payment.Status.PENDING,
        )
        Review.objects.create(
            student=self.student, teacher=self.teacher, rating=5, comment='Good class',
        )

        response = self.client.get('/admin-panel/')

        self.assertEqual(response.status_code, 200)
        platform = {card['title']: card['value'] for card in response.context['platform_cards']}
        self.assertEqual(set(platform), {
            'Total Students', 'Total Teachers', 'Total Quizzes',
            'Total Online Sessions', 'Total Enrollments',
            'Platform Commission', 'Outstanding Platform Dues',
        })
        self.assertEqual(platform['Total Students'], 1)
        self.assertEqual(platform['Total Teachers'], 1)
        self.assertEqual(platform['Total Quizzes'], 1)
        self.assertEqual(platform['Total Online Sessions'], 4)
        self.assertEqual(platform['Total Enrollments'], 1)

        pending = {card['title']: card['value'] for card in response.context['pending_cards']}
        self.assertEqual(pending['Teachers awaiting approval'], 1)
        self.assertEqual(pending['Pending payment verification'], 1)
        self.assertEqual(pending['Pending reviews'], 1)

        classroom = {card['title']: card['value'] for card in response.context['classroom_cards']}
        self.assertEqual(classroom['Recently Completed'], 2)
        self.assertEqual(classroom['Upcoming Sessions'], 1)
        self.assertEqual(classroom['Recording Processing'], 1)
        self.assertEqual(classroom['Recordings Completed'], 1)
        self.assertEqual(classroom['Recordings Failed'], 1)

        self.assertContains(response, 'Platform Statistics')
        self.assertContains(response, 'Pending Actions')
        self.assertContains(response, 'Online Classroom')
        self.assertContains(response, 'Recent Activity')
        self.assertNotContains(response, 'System Health')
        self.assertNotContains(response, 'Gemini AI')
        self.assertNotContains(response, 'Recording Storage')
        self.assertNotContains(response, 'Total Courses')
        self.assertNotContains(response, 'Total Topics')

    def test_dashboard_is_admin_only(self):
        student_client = self.client_class()
        student_client.force_login(self.student)
        response = student_client.get('/admin-panel/')
        self.assertEqual(response.status_code, 302)

    def test_existing_admin_workflow_pages_still_render(self):
        for url in ('/admin-panel/students/', '/admin-panel/teachers/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get('/market/admin/queue/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/admin-panel/payments/')
