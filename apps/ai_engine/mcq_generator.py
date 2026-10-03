import random
from . import prompts
from .gemini_client import chat_json, AIUnavailableError

_LETTERS = ('A', 'B', 'C', 'D')


def _offline_questions(course: str, topic: str, study_level: str, difficulty: str, count: int) -> list:
    """Deterministic fallback MCQs so quizzes remain fully runnable offline."""
    questions = []
    for i in range(1, count + 1):
        correct = random.choice(_LETTERS)
        options = {
            'option_a': f'{topic} concept A-{i}',
            'option_b': f'{topic} concept B-{i}',
            'option_c': f'{topic} concept C-{i}',
            'option_d': f'{topic} concept D-{i}',
        }
        questions.append({
            'question': f'[{study_level} / {difficulty}] Which statement best relates to {topic} (Q{i})?',
            **options,
            'correct_answer': correct,
            'explanation': f'Offline placeholder explanation for {topic} question {i}.',
        })
    return questions


def generate_questions(course: str, topic: str, study_level: str, difficulty: str = 'Medium', count: int = 10) -> list:
    try:
        data = chat_json(
            prompts.MCQ_SYSTEM_PROMPT,
            prompts.mcq_user_prompt(course, topic, study_level, difficulty, count),
        )
        questions = data.get('questions', [])
        if not questions:
            raise ValueError('AI returned no questions')
        return questions[:count]
    except (AIUnavailableError, ValueError):
        return _offline_questions(course, topic, study_level, difficulty, count)
