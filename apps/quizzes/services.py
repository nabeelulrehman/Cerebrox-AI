"""Generate AI quiz content before opening short database write transactions."""
from contextlib import contextmanager
import hashlib
import logging
import secrets
import time

from django.core.cache import cache
from django.db import transaction

from apps.ai_engine.gemini_client import generation_lock_timeout_seconds
from apps.ai_engine.mcq_generator import generate_questions_with_status
from .models import Question, Quiz, QuizAnswer

logger = logging.getLogger(__name__)


class QuizGenerationInProgress(Exception):
    """Raised when an identical quiz request is already being generated."""


@contextmanager
def quiz_generation_lock(student_id, topics, difficulty, num_questions):
    topic_ids = ','.join(str(topic.pk) for topic in topics)
    request_key = f'{student_id}:{topic_ids}:{difficulty}:{num_questions}'
    key_digest = hashlib.sha256(request_key.encode()).hexdigest()
    lock_key = f'ai-quiz:{key_digest}'
    lock_token = secrets.token_urlsafe(16)
    timeout = generation_lock_timeout_seconds(sequential_requests=len(topics))
    if not cache.add(lock_key, lock_token, timeout=timeout):
        raise QuizGenerationInProgress('This quiz is already being generated.')
    try:
        yield
    finally:
        if cache.get(lock_key) == lock_token:
            cache.delete(lock_key)


def create_ai_quiz(student, topic, difficulty, num_questions, extra_topics=None):
    topics = []
    seen_topic_ids = set()
    for selected_topic in [topic, *(extra_topics or [])]:
        if selected_topic.pk not in seen_topic_ids:
            topics.append(selected_topic)
            seen_topic_ids.add(selected_topic.pk)
    study_level = student.effective_study_level

    total_started = time.perf_counter()
    with quiz_generation_lock(student.pk, topics, difficulty, num_questions):
        generation_started = time.perf_counter()
        generated_questions = []
        used_fallback = False
        generation_error = None
        for selected_topic in topics:
            raw_questions, fell_back, error = generate_questions_with_status(
                selected_topic.course.name,
                selected_topic.name,
                study_level,
                difficulty,
                num_questions,
            )
            used_fallback = used_fallback or fell_back
            generation_error = generation_error or error
            generated_questions.extend((selected_topic, item) for item in raw_questions)
        generation_elapsed_ms = round((time.perf_counter() - generation_started) * 1000)

        database_started = time.perf_counter()
        with transaction.atomic():
            question_records = [
                Question.objects.create(
                    topic=selected_topic,
                    question=item['question'],
                    option_a=item['option_a'],
                    option_b=item['option_b'],
                    option_c=item['option_c'],
                    option_d=item['option_d'],
                    correct_answer=item['correct_answer'].upper()[:1],
                    explanation=item.get('explanation', ''),
                    difficulty=difficulty,
                    ai_generated=True,
                )
                for selected_topic, item in generated_questions
            ]
            quiz = Quiz.objects.create(
                student=student,
                topic=topic,
                difficulty=difficulty,
                study_level=study_level,
                total_questions=len(question_records),
                duration_seconds=max(60, len(question_records) * 60),
            )
            quiz.topics.set(topics)
            QuizAnswer.objects.bulk_create(
                [QuizAnswer(quiz=quiz, question=question) for question in question_records]
            )
        database_elapsed_ms = round((time.perf_counter() - database_started) * 1000)

    quiz.used_fallback = used_fallback
    quiz.generation_error = generation_error
    logger.info(
        'AI quiz generated: topic_count=%s question_count=%s used_fallback=%s '
        'generation_elapsed_ms=%s database_elapsed_ms=%s total_elapsed_ms=%s',
        len(topics),
        quiz.total_questions,
        used_fallback,
        generation_elapsed_ms,
        database_elapsed_ms,
        round((time.perf_counter() - total_started) * 1000),
    )
    return quiz
