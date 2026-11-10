from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz

User = get_user_model()


class AdminStudentRankingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='rank-admin', password='StrongPass123', role=User.Role.ADMIN,
        )
        self.student = User.objects.create_user(
            username='student-base', email='base@example.test', password='StrongPass123',
            role=User.Role.STUDENT,
        )
        self.client.force_login(self.admin)

    def make_student(self, username, xp=0, email=None):
        return User.objects.create_user(
            username=username,
            email=email or f'{username}@example.test',
            password='StrongPass123',
            role=User.Role.STUDENT,
            xp_points=xp,
        )

    def test_rank_uses_stored_xp_and_breaks_ties_deterministically(self):
        self.make_student('zeta', xp=50)
        self.make_student('alpha', xp=50)
        self.make_student('champion', xp=100)

        response = self.client.get('/admin-panel/students/')

        self.assertEqual(response.status_code, 200)
        rows = list(response.context['students'])
        self.assertEqual(
            [(row.username, row.xp_points, row.rank) for row in rows],
            [('champion', 100, 1), ('alpha', 50, 2), ('zeta', 50, 3), ('student-base', 0, 4)],
        )
        self.assertEqual(response.context['top_student'].username, 'champion')
        content = response.content.decode()
        self.assertLess(content.index('Top Performer'), content.index('Total Students'))

    def test_empty_xp_and_quiz_history_are_shown_as_real_zero_data(self):
        response = self.client.get('/admin-panel/students/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['students'][0].xp_points, 0)
        self.assertEqual(response.context['students'][0].quiz_attempts, 0)
        self.assertIsNone(response.context['students'][0].average_quiz_score)
        self.assertContains(response, 'No students have earned XP yet')
        self.assertContains(response, '0 · none yet')

    def test_quiz_metrics_include_only_completed_attempts_and_search_matches_email(self):
        course = Course.objects.create(student=self.student, name='Biology')
        topic = Topic.objects.create(course=course, name='Cells')
        Quiz.objects.create(student=self.student, topic=topic, completed=True, percentage=80)
        Quiz.objects.create(student=self.student, topic=topic, completed=True, percentage=90)
        Quiz.objects.create(student=self.student, topic=topic, completed=False, percentage=100)

        response = self.client.get('/admin-panel/students/?q=base%40example.test')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.username for row in response.context['students']], ['student-base'])
        row = response.context['students'][0]
        self.assertEqual(row.quiz_attempts, 2)
        self.assertEqual(row.average_quiz_score, 85)
        self.assertEqual(row.course_count, 1)

    def test_rank_filter_xp_sort_and_pagination(self):
        self.make_student('xp-1', xp=90)
        self.make_student('xp-2', xp=80)
        self.make_student('xp-3', xp=70)
        top_three = self.client.get('/admin-panel/students/?rank=top_3')
        self.assertEqual([row.rank for row in top_three.context['students']], [1, 2, 3])

        low_first = self.client.get('/admin-panel/students/?sort=lowest')
        self.assertEqual(low_first.context['students'][0].username, 'student-base')
        self.assertEqual(low_first.context['students'][0].rank, 4)

        for index in range(25):
            self.make_student(f'page-student-{index:02d}', xp=index)
        page_one = self.client.get('/admin-panel/students/')
        page_two = self.client.get('/admin-panel/students/?page=2')
        self.assertEqual(page_one.context['page_obj'].paginator.num_pages, 2)
        self.assertEqual(page_two.context['page_obj'].number, 2)
        self.assertEqual(page_two.context['page_obj'].paginator.count, 29)

    def test_registration_date_and_last_login_filters(self):
        old_student = self.make_student('old-student')
        old_date = timezone.now() - timedelta(days=120)
        User.objects.filter(pk=old_student.pk).update(created_at=old_date, last_login=old_date)
        User.objects.filter(pk=self.student.pk).update(last_login=timezone.now())

        recent = self.client.get('/admin-panel/students/?joined=30')
        self.assertEqual([row.username for row in recent.context['students']], ['student-base'])
        active = self.client.get('/admin-panel/students/?activity=active')
        self.assertEqual([row.username for row in active.context['students']], ['student-base'])
        inactive = self.client.get('/admin-panel/students/?activity=inactive')
        self.assertEqual([row.username for row in inactive.context['students']], ['old-student'])

    def test_non_admin_cannot_access_student_management(self):
        student_client = self.client_class()
        student_client.force_login(self.student)
        response = student_client.get('/admin-panel/students/')
        self.assertEqual(response.status_code, 302)
