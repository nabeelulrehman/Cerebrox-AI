"""Tests for bugs found in the audit of the pre-existing code base."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from apps.ai_engine.gemini_client import AIUnavailableError
from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz

User = get_user_model()
PW = 'StrongPass123'


class AuditFixTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('aud', 'aud@x.com', PW, role=User.Role.STUDENT)
        self.course = Course.objects.create(student=self.u, name='Math')
        self.topic = Topic.objects.create(course=self.course, name='Algebra')
        self.c = Client()
        self.c.login(username='aud', password=PW)

    def test_login_honours_safe_next_and_rejects_external_redirects(self):
        anon = Client()
        r = anon.post('/accounts/login/?next=/class-quizzes/mine/', {'username': 'aud', 'password': PW})
        self.assertEqual(r.url, '/class-quizzes/mine/')
        r = Client().post('/accounts/login/?next=https://evil.example/phish', {'username': 'aud', 'password': PW})
        self.assertEqual(r.url, '/dashboard/')

    def test_deletes_require_post(self):
        self.assertEqual(self.c.get(f'/learning/topics/{self.topic.id}/delete/').status_code, 405)
        self.assertEqual(self.c.get(f'/learning/courses/{self.course.id}/delete/').status_code, 405)
        self.assertTrue(Course.objects.filter(pk=self.course.id).exists())
        self.assertEqual(self.c.post(f'/learning/topics/{self.topic.id}/delete/').status_code, 302)
        self.assertFalse(Topic.objects.filter(pk=self.topic.id).exists())

    def test_api_returns_404_or_400_instead_of_500(self):
        self.assertEqual(self.c.post('/quizzes/api/generate/', {'topic_id': 999999}).status_code, 404)
        self.assertEqual(self.c.post('/quizzes/api/generate/', {'topic_id': 'abc'}).status_code, 400)
        self.assertEqual(self.c.post('/quizzes/api/generate/', {'topic_id': self.topic.id, 'num_questions': 9999}).status_code, 400)
        self.assertEqual(self.c.post('/quizzes/api/999999/submit/', {}, content_type='application/json').status_code, 404)
        self.assertEqual(self.c.post('/api/ai/notes/', {'topic_id': 999999, 'length': 'medium'}).status_code, 404)

    def test_quiz_cannot_be_resubmitted_through_the_api(self):
        with mock.patch('apps.ai_engine.mcq_generator.chat_json', side_effect=AIUnavailableError('x')):
            self.c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3})
        quiz = Quiz.objects.get()
        correct = {str(a.question_id): a.question.correct_answer for a in quiz.answers.select_related('question')}
        r1 = self.c.post(f'/quizzes/api/{quiz.id}/submit/', {'answers': correct}, content_type='application/json')
        r2 = self.c.post(f'/quizzes/api/{quiz.id}/submit/', {'answers': {}}, content_type='application/json')
        self.assertEqual((r1.status_code, r2.status_code), (200, 409))
        quiz.refresh_from_db()
        self.assertEqual(quiz.percentage, 100.0)

    def test_student_is_warned_when_quiz_is_placeholder_content(self):
        with mock.patch('apps.ai_engine.mcq_generator.chat_json', side_effect=AIUnavailableError('x')):
            r = self.c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3}, follow=True)
        self.assertIn('PLACEHOLDER', r.content.decode())
        good = {'questions': [{'question': f'Q{i}', 'option_a': 'a', 'option_b': 'b', 'option_c': 'c', 'option_d': 'd',
                               'correct_answer': 'A', 'explanation': 'e'} for i in range(3)]}
        with mock.patch('apps.ai_engine.mcq_generator.chat_json', return_value=good):
            r = self.c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3}, follow=True)
        self.assertNotIn('PLACEHOLDER', r.content.decode())

    def test_unfinished_quiz_result_redirects_back_to_the_quiz(self):
        with mock.patch('apps.ai_engine.mcq_generator.chat_json', side_effect=AIUnavailableError('x')):
            self.c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3})
        quiz = Quiz.objects.get()
        r = self.c.get(f'/quizzes/{quiz.id}/result/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/take/', r.url)
