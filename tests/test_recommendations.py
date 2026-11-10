from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.learning.models import Course, Topic
from apps.quizzes.services import create_ai_quiz
from apps.quizzes.evaluation import submit_quiz
from apps.analytics.recommendations import recommendation_for_topic, recommendations_for_student
from unittest.mock import patch
from tests.ai_fixtures import generated_questions

User = get_user_model()


class RecommendationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('stud6', 's6@s.com', 'StudPass123', role=User.Role.STUDENT)
        self.course = Course.objects.create(student=self.student, name='Physics', icon='⚛️')
        self.topic = Topic.objects.create(course=self.course, name='Energy', description='desc')

    def test_recommendation_message_mentions_topic(self):
        rec = recommendation_for_topic('Energy', 40, use_ai=False)
        self.assertEqual(rec['status'], 'Weak')
        self.assertIn('Energy', rec['message'])

    def test_recommendations_for_student_after_quiz(self):
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 4)
        answer_map = {str(qa.question_id): qa.question.correct_answer for qa in quiz.answers.all()}
        submit_quiz(quiz, answer_map)
        recs = recommendations_for_student(self.student, use_ai=False)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]['status'], 'Strong')
