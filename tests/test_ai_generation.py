from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from unittest.mock import patch
from apps.learning.models import Course, Topic, AINote
from apps.ai_engine.notes_generator import generate_notes
from apps.ai_engine.mcq_generator import generate_questions
from apps.ai_engine.gemini_client import AIUnavailableError

User = get_user_model()


class AIGenerationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            'stud3', 's3@s.com', 'StudPass123', role=User.Role.STUDENT, study_level=User.StudyLevel.MATRIC,
        )
        self.course = Course.objects.create(student=self.student, name='Physics', icon='⚛️')
        self.topic = Topic.objects.create(course=self.course, name='Motion', description='desc')

    def test_missing_gemini_key_raises_unavailable_error(self):
        with patch('apps.ai_engine.notes_generator.chat_json', side_effect=AIUnavailableError('GEMINI_API_KEY is not set.')):
            with self.assertRaises(AIUnavailableError):
                generate_notes('Physics', 'Motion', 'Matric', 'medium')

    def test_offline_mcq_fallback_returns_requested_count(self):
        questions = generate_questions('Physics', 'Motion', 'Matric', 'Medium', 5)
        self.assertEqual(len(questions), 5)
        for q in questions:
            self.assertIn(q['correct_answer'], ['A', 'B', 'C', 'D'])

    def test_notes_generate_view_creates_ainote_with_study_level(self):
        c = Client(); c.login(username='stud3', password='StudPass123')
        content = {
            'introduction': 'Intro',
            'explanation': 'Explanation',
            'key_terms': ['force', 'motion'],
            'examples': ['Example 1'],
            'important_points': ['Important 1'],
            'summary': 'Summary',
        }
        with patch('apps.learning.views.generate_notes', return_value=content):
            resp = c.post(f'/learning/topics/{self.topic.id}/notes/generate/', {'length': 'short'})
            self.assertEqual(resp.status_code, 302)
            note = AINote.objects.get(student=self.student, topic=self.topic)
            self.assertEqual(note.study_level, self.student.effective_study_level)
            self.assertEqual(note.content, content)

    def test_notes_dashboard_flow_creates_note_for_course_and_topic(self):
        c = Client(); c.login(username='stud3', password='StudPass123')
        content = {
            'introduction': 'Intro',
            'explanation': 'Explanation',
            'key_terms': ['force', 'motion'],
            'examples': ['Example 1'],
            'important_points': ['Important 1'],
            'summary': 'Summary',
        }
        with patch('apps.learning.views.generate_notes', return_value=content):
            resp = c.post('/learning/notes/', {'course_id': self.course.id, 'topic_name': 'Forces', 'length': 'medium'})
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(AINote.objects.filter(student=self.student, topic__course=self.course, topic__name='Forces').exists())
