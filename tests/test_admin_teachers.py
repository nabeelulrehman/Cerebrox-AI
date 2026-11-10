from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.marketplace.models import Review
from apps.teachers.models import TeacherProfile

User = get_user_model()


class AdminTeacherManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='teacher-admin', password='unused', role=User.Role.ADMIN)
        self.client.force_login(self.admin)

    def make_teacher(self, name, status=TeacherProfile.Status.APPROVED, subjects='Physics, Maths'):
        user = User.objects.create(username=name, email=f'{name}@example.test', password='!', role=User.Role.TEACHER)
        profile = TeacherProfile.objects.create(
            user=user, verification_status=status, subjects=subjects,
            qualifications='BSc', teaching_mode=TeacherProfile.Mode.ONLINE,
        )
        return user, profile

    def make_review(self, teacher, student_name, rating):
        student = User.objects.create(username=student_name, password='!', role=User.Role.STUDENT)
        return Review.objects.create(
            teacher=teacher, student=student, rating=rating,
            status=Review.Status.APPROVED, comment=f'{rating} stars',
        )

    def test_overview_uses_real_profiles_and_approved_reviews(self):
        reviewed, _ = self.make_teacher('reviewed')
        self.make_teacher('waiting', TeacherProfile.Status.PENDING)
        self.make_teacher('rejected', TeacherProfile.Status.REJECTED)
        self.make_review(reviewed, 'reviewer-a', 4)
        Review.objects.create(
            teacher=reviewed,
            student=User.objects.create(username='reviewer-pending', password='!', role=User.Role.STUDENT),
            rating=1, status=Review.Status.PENDING,
        )

        response = self.client.get('/admin-panel/teachers/?status=all')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_teachers'], 3)
        self.assertEqual(response.context['pending_count'], 1)
        self.assertEqual(response.context['approved_count'], 1)
        self.assertEqual(response.context['average_rating'], 4)
        row = next(p for p in response.context['profiles'] if p.user == reviewed)
        self.assertEqual(row.review_count, 1)
        self.assertEqual(row.average_rating, 4)
        self.assertEqual(len(row.recent_reviews), 1)

    def test_leaderboard_weights_review_count_and_uses_stable_top_three(self):
        many_reviews, _ = self.make_teacher('many-reviews', subjects='Physics, Maths, Chemistry')
        one_review, _ = self.make_teacher('one-review', subjects='Physics')
        third, _ = self.make_teacher('third')
        for index in range(8):
            self.make_review(many_reviews, f'many-student-{index}', 5)
        self.make_review(one_review, 'one-student', 5)
        for index in range(10):
            self.make_review(third, f'third-student-{index}', 4)

        response = self.client.get('/admin-panel/teachers/')

        ranks = [(p.user.username, p.rank) for p in response.context['leaderboard']]
        self.assertEqual(ranks[0], ('many-reviews', 1))
        self.assertEqual(ranks[1], ('one-review', 2))
        self.assertEqual(ranks[2], ('third', 3))
        self.assertContains(response, 'Top Performer')
        self.assertEqual(response.context['leaderboard'][0].verified_skill_count, 3)

    def test_search_status_filter_and_pagination(self):
        target, _ = self.make_teacher('needle-teacher', TeacherProfile.Status.APPROVED, 'Quantum Physics')
        self.make_teacher('other-pending', TeacherProfile.Status.PENDING)
        response = self.client.get('/admin-panel/teachers/?status=all&q=needle-teacher%40example.test')
        self.assertEqual([p.user for p in response.context['profiles']], [target])
        self.assertEqual(self.client.get('/admin-panel/teachers/?status=pending').context['page_obj'].paginator.count, 1)
        for index in range(21):
            self.make_teacher(f'paged-{index:02d}', TeacherProfile.Status.PENDING)
        first = self.client.get('/admin-panel/teachers/?status=pending')
        second = self.client.get('/admin-panel/teachers/?status=pending&page=2')
        self.assertEqual(first.context['page_obj'].paginator.num_pages, 2)
        self.assertEqual(len(first.context['profiles']), 20)
        self.assertEqual(len(second.context['profiles']), 2)

    def test_teacher_list_remains_admin_only(self):
        self.client.force_login(User.objects.create(username='student-reader', password='!', role=User.Role.STUDENT))
        response = self.client.get('/admin-panel/teachers/')
        self.assertEqual(response.status_code, 302)
