from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from apps.learning.models import Course, Topic
from apps.quizzes.models import Quiz
from apps.quizzes.services import create_ai_quiz
from apps.quizzes.evaluation import submit_quiz
from unittest.mock import patch
from tests.ai_fixtures import generated_questions

User = get_user_model()


class QuizTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('stud4', 's4@s.com', 'StudPass123', role=User.Role.STUDENT)
        self.course = Course.objects.create(student=self.student, name='Mathematics', icon='∑')
        self.topic = Topic.objects.create(course=self.course, name='Trigonometry', description='desc')

    def test_create_ai_quiz_generates_questions_and_answers(self):
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 5)
        self.assertEqual(quiz.total_questions, 5)
        self.assertEqual(quiz.answers.count(), 5)
        self.assertEqual(quiz.study_level, self.student.effective_study_level)

    def test_submit_quiz_scores_correctly(self):
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 4)
        answer_map = {}
        for qa in quiz.answers.select_related('question').all():
            answer_map[str(qa.question_id)] = qa.question.correct_answer
        submit_quiz(quiz, answer_map)
        quiz.refresh_from_db()
        self.assertTrue(quiz.completed)
        self.assertEqual(quiz.score, 4)
        self.assertEqual(quiz.percentage, 100.0)

    def test_quiz_flow_via_views(self):
        c = Client(); c.login(username='stud4', password='StudPass123')
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            resp = c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3})
        self.assertEqual(resp.status_code, 302)
        quiz = Quiz.objects.get(student=self.student, topic=self.topic)
        answers = {f'question_{a.question_id}': 'A' for a in quiz.answers.all()}
        resp2 = c.post(f'/quizzes/{quiz.id}/submit/', answers)
        self.assertEqual(resp2.status_code, 302)
        resp3 = c.get(resp2.url)
        self.assertEqual(resp3.status_code, 200)

    def test_other_student_cannot_generate_quiz_for_foreign_topic(self):
        other = User.objects.create_user('stud4b', 's4b@s.com', 'StudPass123', role=User.Role.STUDENT)
        c = Client(); c.login(username='stud4b', password='StudPass123')
        resp = c.post(f'/quizzes/topic/{self.topic.id}/generate/', {'difficulty': 'Easy', 'num_questions': 3})
        self.assertEqual(resp.status_code, 404)

    def test_dashboard_quiz_flow_supports_multiple_topics(self):
        second = Topic.objects.create(course=self.course, name='Algebra', description='desc')
        c = Client(); c.login(username='stud4', password='StudPass123')
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            resp = c.post('/quizzes/generate/', {'course_id': self.course.id, 'topic_ids': [self.topic.id, second.id], 'difficulty': 'Medium', 'num_questions': 5})
        self.assertEqual(resp.status_code, 302)
        quiz = Quiz.objects.filter(student=self.student, topic=self.topic).latest('id')
        self.assertGreaterEqual(quiz.topics.count(), 2)
