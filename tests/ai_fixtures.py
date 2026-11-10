"""Deterministic AI payloads for unit tests that exercise downstream quiz behavior."""


def generated_questions(course, topic, study_level, difficulty, count):
    return [
        {
            'question': f'{topic} test question {index}',
            'option_a': 'Correct answer',
            'option_b': 'Distractor B',
            'option_c': 'Distractor C',
            'option_d': 'Distractor D',
            'correct_answer': 'A',
            'explanation': f'Test explanation for {topic}.',
        }
        for index in range(1, count + 1)
    ], False, None
