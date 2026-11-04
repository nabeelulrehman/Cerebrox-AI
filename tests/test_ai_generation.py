from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import connection
from django.core.management import call_command
from config.settings import _normalize_gemini_api_key
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
import httpx
from apps.learning.models import Course, Topic, AINote
from apps.ai_engine.notes_generator import (
    NotesGenerationInProgress,
    generate_notes,
    generate_notes_stream,
    notes_generation_lock,
)
from apps.ai_engine.mcq_generator import (
    generate_questions,
    generate_questions_with_status,
    mcq_output_token_limit,
)
from apps.ai_engine.gemini_client import (
    AIUnavailableError,
    _get_gemini_client,
    chat_json,
    stream_json,
)
from apps.quizzes.services import (
    QuizGenerationInProgress,
    create_ai_quiz,
    quiz_generation_lock,
)

User = get_user_model()


class AIGenerationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            'stud3', 's3@s.com', 'StudPass123', role=User.Role.STUDENT, study_level=User.StudyLevel.MATRIC,
        )
        self.course = Course.objects.create(student=self.student, name='Physics', icon='⚛️')
        self.topic = Topic.objects.create(course=self.course, name='Motion', description='desc')

    def test_dotenv_file_is_resolved_from_project_root(self):
        self.assertEqual(settings.ENV_FILE.parent, settings.BASE_DIR)
        self.assertIn(settings.ENV_FILE.name, {'.env', 'env'})

    def test_gemini_client_receives_configured_key_and_model(self):
        _get_gemini_client.cache_clear()
        with (
            patch('apps.ai_engine.gemini_client.settings.GEMINI_API_KEY', 'test-api-key'),
            patch('apps.ai_engine.gemini_client.settings.GEMINI_MODEL', 'gemini-3.8-flash'),
            patch('google.genai.Client') as client_constructor,
        ):
            client = client_constructor.return_value
            client.models.generate_content.return_value.text = '{"answer": "ok"}'

            try:
                result = chat_json('system', 'question', max_output_tokens=1400)
            finally:
                _get_gemini_client.cache_clear()

        self.assertEqual(result, {'answer': 'ok'})
        self.assertEqual(
            client_constructor.call_args.kwargs['api_key'],
            'test-api-key',
        )
        self.assertEqual(
            client_constructor.call_args.kwargs['http_options'].timeout,
            settings.GEMINI_REQUEST_TIMEOUT_MS,
        )
        self.assertEqual(
            client.models.generate_content.call_args.kwargs['model'],
            'gemini-3.8-flash',
        )
        self.assertEqual(
            client.models.generate_content.call_args.kwargs['config'].max_output_tokens,
            1400,
        )

    def test_api_key_normalization_trims_whitespace_and_outer_quotes(self):
        self.assertEqual(
            _normalize_gemini_api_key(' \r\n"test-key-\nvalue"\t'),
            'test-key-value',
        )
        self.assertEqual(_normalize_gemini_api_key('  \n '), '')

    def test_invalid_key_error_is_clear_and_never_echoes_key(self):
        secret = 'test-secret-key-value'
        invalid_key_error = Exception(
            '400 INVALID_ARGUMENT: API key not valid. reason: API_KEY_INVALID. ' + secret
        )
        invalid_key_error.status_code = 400
        client = Mock()
        client.models.generate_content.side_effect = invalid_key_error
        _get_gemini_client.cache_clear()
        with (
            patch('apps.ai_engine.gemini_client.settings.GEMINI_API_KEY', secret),
            patch('apps.ai_engine.gemini_client._configured_client', return_value=client),
        ):
            with self.assertRaises(AIUnavailableError) as raised:
                chat_json('system', 'question')

        self.assertIn('API_KEY_INVALID', str(raised.exception))
        self.assertIn('Google AI Studio', str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_streaming_invalid_key_error_is_clear_and_never_echoes_key(self):
        secret = 'test-secret-key-value'
        invalid_key_error = Exception(
            '400 INVALID_ARGUMENT: Please pass a valid API key. reason: API_KEY_INVALID. ' + secret
        )
        invalid_key_error.status_code = 400
        client = Mock()
        client.models.generate_content_stream.side_effect = invalid_key_error
        with patch('apps.ai_engine.gemini_client._configured_client', return_value=client):
            stream = stream_json('system', 'question')
            with self.assertRaises(AIUnavailableError) as raised:
                next(stream)

        self.assertIn('API_KEY_INVALID', str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_gemini_config_check_never_prints_key(self):
        secret = 'test-secret-key-value'
        output = StringIO()
        with patch('apps.ai_engine.management.commands.check_gemini_config.settings.GEMINI_API_KEY', secret):
            call_command('check_gemini_config', stdout=output)

        self.assertIn('GEMINI_API_KEY configured: True', output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_notes_stream_yields_chunks_and_returns_validated_notes(self):
        expected = {
            'introduction': 'Intro',
            'explanation': 'Explanation',
            'key_terms': ['force'],
            'examples': ['Example'],
            'important_points': ['Important'],
            'summary': 'Summary',
        }

        def fake_stream(*args, **kwargs):
            yield '{"introduction":"Intro",'
            yield '"explanation":"Explanation","key_terms":["force"],'
            yield '"examples":["Example"],"important_points":["Important"],'
            yield '"summary":"Summary"}'
            return expected

        with patch('apps.ai_engine.notes_generator.stream_json', side_effect=fake_stream):
            stream = generate_notes_stream('Physics', 'Motion', 'Matric', 'short')
            chunks = []
            while True:
                try:
                    chunks.append(next(stream))
                except StopIteration as completed:
                    result = completed.value
                    break

        self.assertEqual(len(chunks), 4)
        self.assertEqual(result['summary'], 'Summary')
        self.assertEqual(result['generated_by'], 'gemini')

    def test_gemini_sdk_streaming_returns_chunks_and_complete_json(self):
        client = Mock()
        client.models.generate_content_stream.return_value = iter([
            SimpleNamespace(text='{"answer":'),
            SimpleNamespace(text='"ok"}'),
        ])
        with (
            patch('apps.ai_engine.gemini_client._configured_client', return_value=client),
            patch('apps.ai_engine.gemini_client.settings.GEMINI_MODEL', 'gemini-3.5-flash-lite'),
            patch('apps.ai_engine.gemini_client.settings.GEMINI_FALLBACK_MODEL', 'gemini-3.8-flash'),
        ):
            stream = stream_json('system', 'question', max_output_tokens=1400)
            chunks = []
            while True:
                try:
                    chunks.append(next(stream))
                except StopIteration as completed:
                    result = completed.value
                    break

        self.assertEqual(chunks, ['{"answer":', '"ok"}'])
        self.assertEqual(result, {'answer': 'ok'})
        request = client.models.generate_content_stream.call_args.kwargs
        self.assertEqual(request['model'], 'gemini-3.5-flash-lite')
        self.assertEqual(request['config'].max_output_tokens, 1400)

    def test_temporary_read_timeout_retries_before_succeeding(self):
        client = Mock()
        client.models.generate_content.side_effect = [
            httpx.ReadTimeout('read timed out'),
            SimpleNamespace(text='{"answer":"ok"}'),
        ]
        with (
            patch('apps.ai_engine.gemini_client._configured_client', return_value=client),
            patch('apps.ai_engine.gemini_client.settings.GEMINI_MAX_RETRIES', 2),
            patch('apps.ai_engine.gemini_client.time.sleep') as sleep,
        ):
            result = chat_json('system', 'question')

        self.assertEqual(result, {'answer': 'ok'})
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_stream_retries_timeout_only_before_emitting_text(self):
        client = Mock()
        client.models.generate_content_stream.side_effect = [
            httpx.ReadTimeout('read timed out'),
            iter([SimpleNamespace(text='{"answer":"ok"}')]),
        ]
        with (
            patch('apps.ai_engine.gemini_client._configured_client', return_value=client),
            patch('apps.ai_engine.gemini_client.settings.GEMINI_MAX_RETRIES', 1),
            patch('apps.ai_engine.gemini_client.time.sleep'),
        ):
            stream = stream_json('system', 'question')
            chunks = list(stream)

        self.assertEqual(chunks, ['{"answer":"ok"}'])
        self.assertEqual(client.models.generate_content_stream.call_count, 2)

        def partial_then_timeout():
            yield SimpleNamespace(text='{"answer":')
            raise httpx.ReadTimeout('read timed out')

        client.models.generate_content_stream.side_effect = [partial_then_timeout()]
        with patch('apps.ai_engine.gemini_client._configured_client', return_value=client):
            stream = stream_json('system', 'question')
            self.assertEqual(next(stream), '{"answer":')
            with self.assertRaises(AIUnavailableError):
                next(stream)

        self.assertEqual(client.models.generate_content_stream.call_count, 3)

    def test_mcq_output_limit_scales_with_question_count(self):
        self.assertEqual(mcq_output_token_limit(3), 1120)
        self.assertEqual(mcq_output_token_limit(10), 2800)
        self.assertEqual(mcq_output_token_limit(25), 6400)

    def test_student_and_teacher_mcq_calls_use_count_based_output_limits(self):
        question = {
            'question': 'Question?',
            'option_a': 'A',
            'option_b': 'B',
            'option_c': 'C',
            'option_d': 'D',
            'correct_answer': 'A',
            'explanation': 'Explanation.',
        }
        with patch(
            'apps.ai_engine.mcq_generator.chat_json',
            return_value={'questions': [question]},
        ) as student_chat:
            generate_questions_with_status('Physics', 'Motion', 'Matric', 'Easy', 3)
        student_chat.assert_called_once()
        self.assertEqual(student_chat.call_args.kwargs['max_output_tokens'], 1120)

        with patch(
            'apps.ai_engine.teacher_ai.chat_json',
            return_value={'questions': [question]},
        ) as teacher_chat:
            from apps.ai_engine.teacher_ai import generate_mcqs_strict

            generate_mcqs_strict('Physics', 'Motion', 'Matric', 'Easy', 3)
        teacher_chat.assert_called_once()
        self.assertEqual(teacher_chat.call_args.kwargs['max_output_tokens'], 1120)

    def test_quiz_generation_does_not_hold_database_transaction_during_gemini_call(self):
        question = {
            'question': 'What is force?',
            'option_a': 'A push or pull',
            'option_b': 'Energy',
            'option_c': 'Power',
            'option_d': 'Speed',
            'correct_answer': 'A',
            'explanation': 'Force is a push or pull.',
        }

        transaction_depth = len(connection.atomic_blocks)

        def generate(*args):
            self.assertEqual(len(connection.atomic_blocks), transaction_depth)
            return [question], False, None

        with patch('apps.quizzes.services.generate_questions_with_status', side_effect=generate):
            quiz = create_ai_quiz(self.student, self.topic, 'Medium', 3)

        self.assertEqual(quiz.total_questions, 1)
        self.assertEqual(quiz.answers.count(), 1)

    def test_quiz_generation_rejects_duplicate_request(self):
        with quiz_generation_lock(self.student.pk, [self.topic], 'Medium', 3):
            with self.assertRaises(QuizGenerationInProgress):
                create_ai_quiz(self.student, self.topic, 'Medium', 3)

    def test_generation_lock_rejects_duplicate_same_student_request(self):
        with notes_generation_lock(self.student.pk, self.topic.pk, 'short'):
            with self.assertRaises(NotesGenerationInProgress):
                with notes_generation_lock(self.student.pk, self.topic.pk, 'short'):
                    self.fail('Duplicate generation unexpectedly acquired the lock.')
        with notes_generation_lock(self.student.pk, self.topic.pk, 'short'):
            pass

    def test_streaming_notes_are_saved_only_after_complete_generation(self):
        c = Client()
        c.login(username='stud3', password='StudPass123')
        content = {
            'introduction': 'Intro',
            'explanation': 'Explanation',
            'key_terms': ['force'],
            'examples': ['Example'],
            'important_points': ['Important'],
            'summary': 'Summary',
            'generated_by': 'gemini',
        }

        def fake_stream(*args, **kwargs):
            yield '{"introduction":"Intro"'
            yield ',"summary":"Summary"}'
            return content

        with patch('apps.learning.views.generate_notes_stream', side_effect=fake_stream):
            response = c.post(
                '/learning/notes/generate/stream/',
                {'topic_id': self.topic.pk, 'length': 'short'},
                HTTP_ACCEPT='text/event-stream',
            )
            output = b''.join(response.streaming_content).decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "chunk"', output)
        self.assertIn('"type": "complete"', output)
        note = AINote.objects.get(student=self.student, topic=self.topic)
        self.assertEqual(note.content['summary'], 'Summary')

    def test_interrupted_stream_does_not_save_partial_notes(self):
        c = Client()
        c.login(username='stud3', password='StudPass123')

        def interrupted_stream(*args, **kwargs):
            yield '{"introduction":"Partial'
            raise AIUnavailableError('Gemini request failed: connection interrupted')

        with patch('apps.learning.views.generate_notes_stream', side_effect=interrupted_stream):
            response = c.post(
                '/learning/notes/generate/stream/',
                {'topic_id': self.topic.pk, 'length': 'short'},
                HTTP_ACCEPT='text/event-stream',
            )
            output = b''.join(response.streaming_content).decode()

        self.assertIn('"type": "error"', output)
        self.assertFalse(AINote.objects.filter(student=self.student, topic=self.topic).exists())

    def test_streaming_notes_endpoint_rejects_duplicate_generation(self):
        c = Client()
        c.login(username='stud3', password='StudPass123')
        with notes_generation_lock(self.student.pk, self.topic.pk, 'short'):
            response = c.post(
                '/learning/notes/generate/stream/',
                {'topic_id': self.topic.pk, 'length': 'short'},
                HTTP_ACCEPT='text/event-stream',
            )
            output = b''.join(response.streaming_content).decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "error"', output)

    def test_missing_gemini_key_raises_unavailable_error(self):
        with patch('apps.ai_engine.notes_generator.chat_json', side_effect=AIUnavailableError('GEMINI_API_KEY is not set.')):
            with self.assertRaises(AIUnavailableError):
                generate_notes('Physics', 'Motion', 'Matric', 'medium')

    def test_short_medium_and_long_notes_use_length_specific_limits(self):
        content = {
            'introduction': 'Intro',
            'explanation': 'Explanation',
            'key_terms': ['term'],
            'examples': ['Example'],
            'important_points': ['Important'],
            'summary': 'Summary',
        }
        cases = (('short', 1400), ('medium', 2800), ('long', 4500))
        with patch(
            'apps.ai_engine.notes_generator.chat_json',
            side_effect=lambda *args, **kwargs: dict(content),
        ) as chat:
            for length, _ in cases:
                notes = generate_notes('Physics', 'Motion', 'Matric', length)
                self.assertEqual(notes['summary'], 'Summary')

        self.assertEqual(
            [call.kwargs['max_output_tokens'] for call in chat.call_args_list],
            [limit for _, limit in cases],
        )

    def test_offline_mcq_fallback_returns_requested_count(self):
        with patch(
            'apps.ai_engine.mcq_generator.chat_json',
            side_effect=AIUnavailableError('Gemini unavailable in this offline fallback test.'),
        ):
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
