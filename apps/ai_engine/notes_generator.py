from . import prompts
from .gemini_client import chat_json, AIUnavailableError


def generate_notes(course: str, topic: str, study_level: str, length: str = 'medium') -> dict:
    """Generate real AI study notes for the selected course/topic."""
    data = chat_json(
        prompts.NOTES_SYSTEM_PROMPT,
        prompts.notes_user_prompt(course, topic, study_level, length),
    )

    if not isinstance(data, dict):
        raise AIUnavailableError('Gemini returned an invalid notes payload.')

    required = ['introduction', 'explanation', 'key_terms', 'examples', 'important_points', 'summary']
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
