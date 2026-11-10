from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.learning.models import Course, Topic
from apps.quizzes.services import create_ai_quiz
from apps.quizzes.evaluation import submit_quiz
from apps.analytics.performance import student_overview
from apps.analytics.weak_topics import classify, weak_topics_for
from unittest.mock import patch
from tests.ai_fixtures import generated_questions

User = get_user_model()


class PerformanceTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user('stud5', 's5@s.com', 'StudPass123', role=User.Role.STUDENT)
        self.course = Course.objects.create(student=self.student, name='Mathematics', icon='∑')
        self.topic = Topic.objects.create(course=self.course, name='Geometry', description='desc')

    def test_classify_thresholds(self):
        self.assertEqual(classify(85), 'Strong')
        self.assertEqual(classify(60), 'Needs Practice')
        self.assertEqual(classify(30), 'Weak')

    def test_overview_reflects_completed_quiz(self):
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 4)
        answer_map = {str(qa.question_id): 'Z' for qa in quiz.answers.all()}  # all wrong -> 0%
        submit_quiz(quiz, answer_map)
        overview = student_overview(self.student)
        self.assertEqual(overview['total_quizzes'], 1)
        self.assertEqual(overview['average_score'], 0)

    def test_weak_topic_detected_on_low_score(self):
        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generated_questions):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 4)
        answer_map = {str(qa.question_id): 'Z' for qa in quiz.answers.all()}
        submit_quiz(quiz, answer_map)
        weak = weak_topics_for(self.student)
        self.assertEqual(weak[0]['status'], 'Weak')
