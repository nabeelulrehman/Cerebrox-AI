from . import prompts
from .gemini_client import chat_json, AIUnavailableError


def phrase_recommendation(topic: str, score_percent: float, status: str, fallback_text: str) -> str:
    """Optionally turn a rule-based recommendation into natural language via AI.
    Falls back to the plain rule-based text (never AI-dependent for correctness)."""
    try:
        data = chat_json(
            prompts.RECOMMENDATION_SYSTEM_PROMPT,
            prompts.recommendation_user_prompt(topic, score_percent, status),
        )
        return data.get('message') or fallback_text
    except AIUnavailableError:
        return fallback_text
