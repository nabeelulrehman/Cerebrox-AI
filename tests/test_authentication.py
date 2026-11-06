from io import BytesIO
import tempfile

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.test.utils import override_settings
from django.contrib.auth import get_user_model

from apps.teachers.models import TeacherProfile

User = get_user_model()
TEACHER_PASSWORD = 'StrongPass123'


def image_upload(name, image_format, size=(24, 24), trailing_bytes=b''):
    buffer = BytesIO()
    Image.new('RGB', size, 'purple').save(buffer, format=image_format)
    return SimpleUploadedFile(
        name,
        buffer.getvalue() + trailing_bytes,
        content_type=f'image/{image_format.lower()}',
    )


class AuthenticationTests(TestCase):
    def test_profile_crop_interface_uses_existing_avatar_upload_field(self):
        student = User.objects.create_user(
            'crop-student', 'crop-student@example.com', TEACHER_PASSWORD,
        )
        client = Client()
        client.force_login(student)
        html = client.get('/accounts/profile/').content.decode()

        self.assertIn('name="avatar"', html)
        self.assertIn('accept="image/jpeg,image/png,image/webp"', html)
        self.assertIn('data-profile-crop-dialog', html)
        self.assertIn('data-profile-crop-preview', html)
        self.assertIn('data-profile-crop-preview-canvas', html)
        self.assertIn('data-profile-crop-zoom-in', html)
        self.assertIn('data-profile-crop-zoom-out', html)
        self.assertIn('Crop &amp; Save', html)
        self.assertIn('cropperjs@1.6.2', html)
        self.assertIn('/static/js/profile_avatar_crop.js?v=3', html)

    def test_profile_rejects_unsupported_and_oversized_avatars_without_replacing_current(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                student = User.objects.create_user(
                    'crop-validation', 'crop-validation@example.com', TEACHER_PASSWORD,
                )
                student.avatar.save('existing.png', image_upload('existing.png', 'PNG'), save=True)
                existing_name = student.avatar.name
                client = Client()
                client.force_login(student)
                profile_data = {
                    'first_name': 'Crop',
                    'last_name': 'Validation',
                    'email': student.email,
                    'bio': '',
                    'study_level': 'undergraduate',
                    'study_level_custom': '',
                }

                unsupported = client.post(
                    '/accounts/profile/',
                    {**profile_data, 'avatar': image_upload('animated.gif', 'GIF')},
                )
                self.assertEqual(unsupported.status_code, 200)
                self.assertFormError(
                    unsupported.context['form'],
                    'avatar',
                    'Upload a JPEG, PNG, or WebP image.',
                )
                student.refresh_from_db()
                self.assertEqual(student.avatar.name, existing_name)

                oversized = client.post(
                    '/accounts/profile/',
                    {
                        **profile_data,
                        'avatar': image_upload(
                            'too-large.jpg',
                            'JPEG',
                            trailing_bytes=b'x' * (5 * 1024 * 1024),
                        ),
                    },
                )
                self.assertEqual(oversized.status_code, 200)
                self.assertFormError(
                    oversized.context['form'],
                    'avatar',
                    'Profile images must be 5 MB or smaller.',
                )
                student.refresh_from_db()
                self.assertEqual(student.avatar.name, existing_name)

    def test_profile_accepts_and_saves_square_cropped_avatar(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                student = User.objects.create_user(
                    'cropped-student', 'cropped-student@example.com', TEACHER_PASSWORD,
                )
                client = Client()
                client.force_login(student)
                response = client.post('/accounts/profile/', {
                    'first_name': 'Cropped',
                    'last_name': 'Student',
                    'email': student.email,
                    'bio': '',
                    'study_level': 'undergraduate',
                    'study_level_custom': '',
                    'avatar': image_upload('portrait-cropped.jpg', 'JPEG', size=(512, 512)),
                })

                self.assertRedirects(response, '/accounts/profile/')
                student.refresh_from_db()
                with Image.open(student.avatar.path) as saved_avatar:
                    self.assertEqual(saved_avatar.format, 'JPEG')
                    self.assertEqual(saved_avatar.size, (512, 512))
                updated_profile = client.get('/accounts/profile/').content.decode()
                self.assertIn(f'src="{student.avatar.url}"', updated_profile)

    def test_shared_header_shows_profile_avatar_or_user_initials(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                student = User.objects.create_user(
                    'avatar-student',
                    'avatar-student@example.com',
                    TEACHER_PASSWORD,
                    first_name='Ada',
                    last_name='Lovelace',
                )
                student.avatar.save(
                    'student-avatar.png',
                    SimpleUploadedFile('student-avatar.png', b'profile image', content_type='image/png'),
                    save=True,
                )
                student_client = Client()
                student_client.force_login(student)
                student_page = student_client.get('/dashboard/').content.decode()
                self.assertIn(f'src="{student.avatar.url}"', student_page)
                self.assertIn('class="site-avatar__image"', student_page)
                self.assertIn('data-avatar-preview', student_page)
                self.assertIn('data-avatar-dialog', student_page)
                self.assertIn('alt="Profile picture of Ada"', student_page)
                self.assertIn('static/js/profile_avatar.js', student_page)

                teacher_with_avatar = User.objects.create_user(
                    'teacher-avatar',
                    'teacher-avatar@example.com',
                    TEACHER_PASSWORD,
                    role=User.Role.TEACHER,
                    first_name='Grace',
                    last_name='Hopper',
                )
                teacher_with_avatar.avatar.save(
                    'teacher-avatar.png',
                    SimpleUploadedFile('teacher-avatar.png', b'teacher profile image', content_type='image/png'),
                    save=True,
                )
                avatar_teacher_client = Client()
                avatar_teacher_client.force_login(teacher_with_avatar)
                teacher_avatar_page = avatar_teacher_client.get('/accounts/profile/').content.decode()
                self.assertIn(f'src="{teacher_with_avatar.avatar.url}"', teacher_avatar_page)
                self.assertIn('data-avatar-preview', teacher_avatar_page)
                self.assertIn('data-avatar-dialog', teacher_avatar_page)

                teacher = User.objects.create_user(
                    'avatar-teacher',
                    'avatar-teacher@example.com',
                    TEACHER_PASSWORD,
                    role=User.Role.TEACHER,
                    first_name='Alan',
                    last_name='Turing',
                )
                teacher_client = Client()
                teacher_client.force_login(teacher)
                teacher_page = teacher_client.get('/accounts/profile/').content.decode()
                self.assertIn('class="site-avatar__initials" aria-hidden="true">AT</span>', teacher_page)
                self.assertNotIn('class="site-avatar__image"', teacher_page)
                self.assertNotIn('data-avatar-dialog', teacher_page)

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
        for item in ('AI LEARNING', 'My Courses', 'AI Notes', 'AI Quiz', 'Performance',
                     'Recommendations', 'Profile', 'TEACHER ASSISTED', 'Find Teachers', 'My Classes',
                     'Teacher-Assigned Quizzes', 'Shared Study Materials',
                     'Teaching Sessions', 'My Messages'):
            self.assertIn(item, student_page)
        student_sidebar = student_page.split('<aside id="sidebar"', 1)[1].split('</aside>', 1)[0]
        profile_link = '<nav class="student-sidebar__links" aria-label="Account">'
        self.assertIn(profile_link, student_sidebar)
        self.assertGreater(student_sidebar.index(profile_link), student_sidebar.rindex('</section>'))
        self.assertEqual(student_sidebar.count('>👤 Profile</a>'), 1)
        self.assertIn('<h2 class="student-sidebar__heading" id="ai-learning-heading">', student_page)
        self.assertIn('<h2 class="student-sidebar__heading student-sidebar__heading--teacher" id="teacher-assisted-heading">', student_page)
        self.assertNotIn('student-sidebar__toggle', student_page)
        self.assertNotIn('student-sidebar__chevron', student_page)
        teacher_assisted_page = student_client.get('/classes/mine/').content.decode()
        self.assertIn('class="navlink active">🏫 My Classes', teacher_assisted_page)
        self.assertIn('TEACHER ASSISTED', teacher_assisted_page)

        teacher_client = Client()
        teacher_client.force_login(teacher)
        teacher_page = teacher_client.get('/accounts/profile/').content.decode()
        for item in ('Teacher dashboard', 'My classes', 'Verify students',
                     'Materials', 'Sessions', 'Messages', 'Teacher profile'):
            self.assertIn(item, teacher_page)
        for item in ('My Courses', 'AI Notes', 'Performance', 'Recommendations',
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
