"""Gemini provider wrapper, kept behind a provider-neutral JSON interface."""
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class AIUnavailableError(Exception):
    """Raised when no AI provider is configured or the call fails."""


def is_configured() -> bool:
    api_key = (settings.GEMINI_API_KEY or '').strip()
    return bool(api_key)


def _safe_error_details(exc: Exception) -> str:
    status_code = getattr(exc, 'status_code', None)
    request_id = getattr(exc, 'request_id', None)
    message = str(exc)
    api_key = (settings.GEMINI_API_KEY or '').strip()
    if api_key:
        message = message.replace(api_key, '[redacted]')
    details = [f'type={type(exc).__name__}', f'message={message}']
    if status_code is not None:
        details.append(f'http_status={status_code}')
    if request_id:
        details.append(f'request_id={request_id}')
    return ', '.join(details)


def _is_capacity_error(exc: Exception) -> bool:
    """Return whether Gemini reported a temporary overload or rate limit."""
    status_code = getattr(exc, 'status_code', None)
    message = str(exc).upper()
    return status_code in (429, 503) or any(
        marker in message
        for marker in ('429 TOO MANY REQUESTS', '503 UNAVAILABLE', 'RESOURCE_EXHAUSTED')
    )


def chat_json(system_prompt: str, user_prompt: str) -> dict:
    """
    Send a prompt pair to the configured model and parse the JSON response.
    Raises AIUnavailableError when the backend is not configured or the API
    call returns an unusable result.
    """
    api_key = (settings.GEMINI_API_KEY or '').strip()
    if not api_key:
        logger.error(
            'Gemini request not started: API key detected=False, model=%s. '
            'Set GEMINI_API_KEY in the backend env file.',
            settings.GEMINI_MODEL,
        )
        raise AIUnavailableError('Gemini API key is missing. Set GEMINI_API_KEY in the backend env file.')

    try:
        from google import genai
        from google.genai import types
        logger.info(
            'Initializing Gemini client: API key detected=True, model=%s',
            settings.GEMINI_MODEL,
        )
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type='application/json',
            temperature=0.5,
        )
        model = settings.GEMINI_MODEL
        logger.info('Sending Gemini generate_content request: model=%s', model)
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
        except Exception as exc:
            fallback_model = getattr(settings, 'GEMINI_FALLBACK_MODEL', '')
            if not _is_capacity_error(exc) or not fallback_model or fallback_model == model:
                raise
            logger.warning(
                'Gemini model %s is temporarily unavailable; retrying with fallback model=%s',
                model,
                fallback_model,
            )
            model = fallback_model
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
        raw = response.text
        logger.info(
            'Gemini response received: model=%s', model,
        )
        if raw is None or not str(raw).strip():
            raise AIUnavailableError('Gemini returned an empty response.')
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise AIUnavailableError('Gemini returned a non-object JSON payload.')
        return parsed
    except AIUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - any provider/parsing failure funnels here
        logger.error('Gemini request failed: %s', _safe_error_details(exc))
        raise AIUnavailableError(f'Gemini request failed: {_safe_error_details(exc)}') from exc
