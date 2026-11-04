from django.test import TestCase, Client
from django.contrib.auth import get_user_model

from apps.teachers.models import TeacherProfile

User = get_user_model()
TEACHER_PASSWORD = 'StrongPass123'


class AuthenticationTests(TestCase):
    def test_profile_menu_renders_navigation_for_the_active_role(self):
        student = User.objects.create_user(
            'sidebar-student',
            'sidebar-student@example.com',
            TEACHER_PASSWORD,
        )
        teacher = User.objects.create_user(
            'sidebar-teacher',
            'sidebar-teacher@example.com',
            TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        TeacherProfile.objects.create(
            user=teacher,
            qualifications='BSc Education',
            subjects='Maths',
            verification_status=TeacherProfile.Status.APPROVED,
        )
        admin = User.objects.create_user(
            'sidebar-admin',
            'sidebar-admin@example.com',
            TEACHER_PASSWORD,
            role=User.Role.ADMIN,
        )

        student_client = Client()
        student_client.force_login(student)
        student_page = student_client.get('/accounts/profile/').content.decode()
        for item in ('My Courses', 'Notes', 'Quiz', 'Performance', 'Recommendations',
                     'Profile', 'Teacher-assisted', 'Find Teachers', 'My Classes',
                     'Teacher-Assigned Quizzes', 'Shared Study Materials',
                     'Teaching Sessions', 'My Messages'):
            self.assertIn(item, student_page)

        teacher_client = Client()
        teacher_client.force_login(teacher)
        teacher_page = teacher_client.get('/accounts/profile/').content.decode()
        for item in ('Teacher dashboard', 'My classes', 'Verify students',
                     'Materials', 'Sessions', 'Messages', 'Teacher profile'):
            self.assertIn(item, teacher_page)
        for item in ('My Courses', 'Notes', 'Performance', 'Recommendations',
                     'Find Teachers', 'Teacher-Assigned Quizzes'):
            self.assertNotIn(item, teacher_page)

        admin_client = Client()
        admin_client.force_login(admin)
        admin_page = admin_client.get('/accounts/profile/').content.decode()
        for item in ('Overview', 'Students', 'Teacher approvals', 'Payments'):
            self.assertIn(item, admin_page)
        for item in ('My Courses', 'Find Teachers', 'Teacher dashboard'):
            self.assertNotIn(item, admin_page)

    def test_role_restricted_navigation_destinations_reject_opposite_role(self):
        student = User.objects.create_user(
            'restricted-student',
            'restricted-student@example.com',
            TEACHER_PASSWORD,
        )
        teacher = User.objects.create_user(
            'restricted-teacher',
            'restricted-teacher@example.com',
            TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        TeacherProfile.objects.create(
            user=teacher,
            qualifications='BSc Education',
            subjects='Maths',
        )
        student_client = Client()
        student_client.force_login(student)
        for url in ('/teachers/dashboard/', '/classes/teach/'):
            self.assertEqual(student_client.get(url).status_code, 403, url)

        teacher_client = Client()
        teacher_client.force_login(teacher)
        for url in ('/learning/courses/', '/learning/notes/', '/quizzes/generate/',
                    '/analytics/performance/', '/analytics/recommendations/',
                    '/classes/mine/', '/class-quizzes/mine/'):
            self.assertEqual(teacher_client.get(url).status_code, 403, url)
        for url in (
            '/api/learning/courses/',
            '/analytics/api/performance/',
            '/api/dashboard/summary/',
        ):
            self.assertEqual(teacher_client.get(url).status_code, 403, url)
        self.assertEqual(
            teacher_client.post(
                '/api/ai/notes/generate/',
                data='{}',
                content_type='application/json',
            ).status_code,
            403,
        )
        self.assertEqual(
            teacher_client.post(
                '/quizzes/api/generate/',
                data='{}',
                content_type='application/json',
            ).status_code,
            403,
        )

        self.assertEqual(student_client.get('/api/learning/courses/').status_code, 200)

    def test_registration_page_offers_student_and_teacher_options(self):
        response = Client().get('/accounts/register/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Register as Student')
        self.assertContains(response, 'Register as Teacher')
        self.assertContains(response, '/teachers/register/')

        teacher_response = Client().get('/teachers/register/')
        self.assertEqual(teacher_response.status_code, 200)
        self.assertContains(teacher_response, 'Short professional bio')
        self.assertContains(teacher_response, 'Register as Student')

    def test_register_creates_student(self):
        c = Client()
        resp = c.post('/accounts/register/', {
            'username': 'newstudent', 'first_name': 'New', 'last_name': 'Student',
            'email': 'new@student.com', 'study_level': User.StudyLevel.UNDERGRADUATE,
            'password1': 'StrongPass123', 'password2': 'StrongPass123',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/learning/courses/', resp.url)  # new registrations land on course setup
        user = User.objects.get(username='newstudent')
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(user.study_level, User.StudyLevel.UNDERGRADUATE)
        c.get('/accounts/logout/')
        login = c.post('/accounts/login/', {
            'username': 'newstudent',
            'password': 'StrongPass123',
        })
        self.assertRedirects(login, '/dashboard/', fetch_redirect_response=False)

    def test_login_with_wrong_password_fails(self):
        User.objects.create_user('bob', 'bob@x.com', 'CorrectPass123')
        c = Client()
        resp = c.post('/accounts/login/', {'username': 'bob', 'password': 'WrongPass'})
        self.assertEqual(resp.status_code, 200)  # re-renders login form with error
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_dashboard_requires_login(self):
        c = Client()
        resp = c.get('/dashboard/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp.url)

    def test_student_login_logout_and_login_again(self):
        User.objects.create_user('student1', 'student1@example.com', TEACHER_PASSWORD)
        c = Client()

        response = c.post('/accounts/login/', {
            'username': 'student1',
            'password': TEACHER_PASSWORD,
        })
        self.assertRedirects(response, '/dashboard/', fetch_redirect_response=False)
        self.assertEqual(str(c.session['_auth_user_id']), str(User.objects.get(username='student1').pk))

        c.get('/accounts/logout/')
        self.assertNotIn('_auth_user_id', c.session)
        response = c.post('/accounts/login/', {
            'username': 'student1',
            'password': TEACHER_PASSWORD,
        })
        self.assertRedirects(response, '/dashboard/', fetch_redirect_response=False)

    def test_teacher_registration_logout_and_relogin_with_same_credentials(self):
        c = Client()
        response = c.post('/teachers/register/', {
            'username': 'teacher1',
            'first_name': 'Teacher',
            'email': 'teacher1@example.com',
            'password1': TEACHER_PASSWORD,
            'password2': TEACHER_PASSWORD,
            'bio': 'I make science approachable with practical examples.',
            'qualifications': 'BSc Education',
            'subjects': 'Maths',
            'experience_years': 2,
            'teaching_mode': TeacherProfile.Mode.ONLINE,
        })
        self.assertRedirects(response, '/teachers/dashboard/', fetch_redirect_response=False)

        teacher = User.objects.get(username='teacher1')
        self.assertEqual(teacher.role, User.Role.TEACHER)
        self.assertTrue(teacher.is_active)
        self.assertFalse(teacher.is_staff)
        self.assertFalse(teacher.is_superuser)
        self.assertTrue(teacher.check_password(TEACHER_PASSWORD))
        self.assertEqual(teacher.teacher_profile.user_id, teacher.pk)
        self.assertEqual(
            teacher.teacher_profile.bio,
            'I make science approachable with practical examples.',
        )
        self.assertEqual(teacher.teacher_profile.verification_status, TeacherProfile.Status.PENDING)
        dashboard = c.get('/teachers/dashboard/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Pending review')

        c.get('/accounts/logout/')
        self.assertNotIn('_auth_user_id', c.session)
        response = c.post('/accounts/login/', {
            'username': 'teacher1',
            'password': TEACHER_PASSWORD,
        })
        self.assertRedirects(response, '/teachers/dashboard/', fetch_redirect_response=False)
        self.assertEqual(str(c.session['_auth_user_id']), str(teacher.pk))
        self.assertEqual(c.get('/teachers/dashboard/').status_code, 200)
        self.assertEqual(c.get('/dashboard/').url, '/teachers/dashboard/')

    def test_teacher_registration_rejects_duplicate_email_and_does_not_create_account(self):
        User.objects.create_user('existing', 'duplicate@example.com', TEACHER_PASSWORD)
        response = Client().post('/teachers/register/', {
            'username': 'new-teacher',
            'first_name': 'New',
            'last_name': 'Teacher',
            'email': 'DUPLICATE@example.com',
            'password1': TEACHER_PASSWORD,
            'password2': TEACHER_PASSWORD,
            'qualifications': 'BSc Education',
            'subjects': 'Maths',
            'experience_years': 2,
            'teaching_mode': TeacherProfile.Mode.ONLINE,
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='new-teacher').exists())

    def test_teacher_login_accepts_registered_email(self):
        teacher = User.objects.create_user(
            'teacher-email', 'teacher-email@example.com', TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        TeacherProfile.objects.create(
            user=teacher, qualifications='BSc Education', subjects='Maths',
        )

        response = Client().post('/accounts/login/', {
            'username': ' TEACHER-EMAIL@example.com ',
            'password': TEACHER_PASSWORD,
        })
        self.assertRedirects(response, '/teachers/dashboard/', fetch_redirect_response=False)

    def test_existing_teacher_account_can_log_in_without_recreation(self):
        teacher = User.objects.create_user(
            'existing-teacher', 'existing@example.com', TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        TeacherProfile.objects.create(
            user=teacher, qualifications='BSc Education', subjects='Maths',
        )

        c = Client()
        response = c.post('/accounts/login/', {
            'username': 'existing-teacher',
            'password': TEACHER_PASSWORD,
        })

        self.assertRedirects(response, '/teachers/dashboard/', fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(username='existing-teacher').count(), 1)

    def test_invalid_password_and_inactive_teacher_are_rejected(self):
        active_teacher = User.objects.create_user(
            'active-teacher', 'active@example.com', TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        TeacherProfile.objects.create(
            user=active_teacher, qualifications='BSc Education', subjects='Maths',
        )
        teacher = User.objects.create_user(
            'inactive-teacher', 'inactive@example.com', TEACHER_PASSWORD,
            role=User.Role.TEACHER, is_active=False,
        )
        TeacherProfile.objects.create(
            user=teacher, qualifications='BSc Education', subjects='Maths',
        )
        c = Client()
        for username, password in (
            ('active-teacher', 'wrong-password'),
            ('inactive-teacher', TEACHER_PASSWORD),
        ):
            response = c.post('/accounts/login/', {'username': username, 'password': password})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('_auth_user_id', c.session)

    def test_missing_teacher_profile_is_reported_without_crashing(self):
        teacher = User.objects.create_user(
            'teacher-without-profile', 'orphan@example.com', TEACHER_PASSWORD,
            role=User.Role.TEACHER,
        )
        c = Client()
        response = c.post('/accounts/login/', {
            'username': teacher.username,
            'password': TEACHER_PASSWORD,
        })
        self.assertRedirects(response, '/teachers/dashboard/', fetch_redirect_response=False)

        dashboard = c.get('/teachers/dashboard/')
        self.assertEqual(dashboard.status_code, 409)
        self.assertContains(dashboard, 'teacher profile is missing', status_code=409)
        self.assertTrue(c.session.get('_auth_user_id'))
