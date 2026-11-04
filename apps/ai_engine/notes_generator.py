from contextlib import contextmanager
import secrets

from django.core.cache import cache

from . import prompts
from .gemini_client import (
    AIUnavailableError,
    chat_json,
    generation_lock_timeout_seconds,
    stream_json,
)

_OUTPUT_TOKEN_LIMITS = {'short': 1400, 'medium': 2800, 'long': 4500}


class NotesGenerationInProgress(Exception):
    """Raised when the same student already has this notes request in progress."""


def _validate_notes(data: dict) -> dict:
    required = ('introduction', 'explanation', 'key_terms', 'examples', 'important_points', 'summary')
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise AIUnavailableError('Gemini response was missing required notes sections.')

    for key in ('key_terms', 'examples', 'important_points'):
        value = data.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value:
            raise AIUnavailableError(f'Gemini response contained an empty or invalid {key} section.')
        data[key] = value

    data.setdefault('generated_by', 'gemini')
    return data


def _notes_request(course: str, topic: str, study_level: str, length: str):
    token_limit = _OUTPUT_TOKEN_LIMITS.get(length, _OUTPUT_TOKEN_LIMITS['medium'])
    return (
        prompts.NOTES_SYSTEM_PROMPT,
        prompts.notes_user_prompt(course, topic, study_level, length),
        token_limit,
    )


def generate_notes(course: str, topic: str, study_level: str, length: str = 'medium') -> dict:
    """Generate validated, structured study notes."""
    system_prompt, user_prompt, token_limit = _notes_request(course, topic, study_level, length)
    data = chat_json(system_prompt, user_prompt, max_output_tokens=token_limit)
    if not isinstance(data, dict):
        raise AIUnavailableError('Gemini returned an invalid notes payload.')
    return _validate_notes(data)


def generate_notes_stream(course: str, topic: str, study_level: str, length: str = 'medium'):
    """Yield Gemini text chunks and return validated notes when the stream completes."""
    system_prompt, user_prompt, token_limit = _notes_request(course, topic, study_level, length)
    data = yield from stream_json(system_prompt, user_prompt, max_output_tokens=token_limit)
    if not isinstance(data, dict):
        raise AIUnavailableError('Gemini returned an invalid notes payload.')
    return _validate_notes(data)


@contextmanager
def notes_generation_lock(student_id: int, topic_id: int, length: str):
    """Prevent duplicate concurrent requests for one student's topic and note length."""
    lock_key = f'ai-notes:{student_id}:{topic_id}:{length}'
    lock_token = secrets.token_urlsafe(16)
    timeout = generation_lock_timeout_seconds()
    if not cache.add(lock_key, lock_token, timeout=timeout):
        raise NotesGenerationInProgress('Notes for this topic and length are already being generated.')
    try:
        yield
    finally:
        if cache.get(lock_key) == lock_token:
            cache.delete(lock_key)
