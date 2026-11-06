from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from apps.learning.models import Course

User = get_user_model()


class CourseTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin1', 'a@a.com', 'AdminPass123', role=User.Role.ADMIN)
        self.student = User.objects.create_user('stud1', 's@s.com', 'StudPass123', role=User.Role.STUDENT)
        self.other_student = User.objects.create_user('stud1b', 's1b@s.com', 'StudPass123', role=User.Role.STUDENT)

    def test_student_can_create_own_course(self):
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.post('/learning/courses/', {'name': 'Chemistry', 'icon': '🧪', 'description': 'desc'})
        self.assertEqual(resp.status_code, 302)
        course = Course.objects.get(name='Chemistry')
        self.assertEqual(course.student, self.student)

    def test_student_only_sees_own_courses(self):
        Course.objects.create(student=self.student, name='Biology', icon='🧬')
        Course.objects.create(student=self.other_student, name='History', icon='📜')
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.get('/learning/courses/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Biology')
        self.assertNotContains(resp, 'History')

    def test_courses_page_uses_one_card_and_add_course_dialog(self):
        Course.objects.create(student=self.student, name='Biology', icon='🧬')
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.get('/learning/courses/')

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'My Courses')
        self.assertContains(resp, 'Add New Course')
        self.assertContains(resp, 'course-dialog')
        self.assertContains(resp, 'Biology')
        self.assertNotContains(resp, 'grid md:grid-cols-3')
        self.assertContains(c.get('/learning/courses/?add=1'), 'data-open-on-load="true"')

    def test_invalid_course_form_reopens_dialog(self):
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.post('/learning/courses/', {'name': '', 'icon': '🧬'})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-open-on-load="true"')

    def test_admin_panel_no_longer_creates_global_courses(self):
        c = Client(); c.login(username='admin1', password='AdminPass123')
        resp = c.get('/admin-panel/')
        self.assertEqual(resp.status_code, 200)
        resp2 = c.get('/admin-panel/subjects/')
        self.assertEqual(resp2.status_code, 404)  # route intentionally removed

    def test_student_can_create_course_with_multiple_topics(self):
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.post('/learning/courses/', {
            'name': 'Database Systems',
            'icon': '🗄️',
            'description': 'Learn DBMS concepts.',
            'topics_raw': 'Introduction to DBMS\nER Model\nSQL\nNormalization',
        })
        self.assertEqual(resp.status_code, 302)
        course = Course.objects.get(student=self.student, name='Database Systems')
        self.assertEqual(course.topics.count(), 4)
        self.assertTrue(course.topics.filter(name='SQL').exists())

    def test_student_can_add_topic_to_existing_course(self):
        course = Course.objects.create(student=self.student, name='Database Systems', icon='🗄️')
        c = Client(); c.login(username='stud1', password='StudPass123')
        resp = c.post(f'/learning/courses/{course.id}/topics/', {'name': 'Database Security', 'description': 'sec'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(course.topics.filter(name='Database Security').exists())
