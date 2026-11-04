"""Gemini provider wrapper, kept behind a provider-neutral JSON interface."""
import json
import logging
import math
import time
from functools import lru_cache

import httpx
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


def _is_invalid_api_key_error(exc: Exception) -> bool:
    message = str(exc).upper()
    return (
        getattr(exc, 'status_code', None) == 400
        and (
            'API_KEY_INVALID' in message
            or 'API KEY NOT VALID' in message
            or 'INVALID API KEY' in message
        )
    )


def _invalid_api_key_message() -> str:
    return (
        'Gemini rejected GEMINI_API_KEY (API_KEY_INVALID). Create a valid key in '
        'Google AI Studio and update GEMINI_API_KEY in the backend .env file.'
    )


def _is_retryable_error(exc: Exception) -> bool:
    status_code = getattr(exc, 'status_code', None)
    return isinstance(exc, httpx.TransportError) or status_code in {
        408, 429, 500, 502, 503, 504,
    }


def _retry_delay(attempt: int) -> float:
    return max(0.0, settings.GEMINI_RETRY_BASE_SECONDS) * (2 ** attempt)


def generation_lock_timeout_seconds(sequential_requests: int = 1) -> int:
    retries = max(0, settings.GEMINI_MAX_RETRIES)
    retry_wait = sum(_retry_delay(attempt) for attempt in range(retries))
    one_model_budget = (
        settings.GEMINI_REQUEST_TIMEOUT_MS / 1000 * (retries + 1) + retry_wait
    )
    attempts_with_fallback = one_model_budget * 2
    return max(180, math.ceil((attempts_with_fallback + 60) * sequential_requests))


def _call_with_retries(request, *, model: str, operation: str):
    max_retries = max(0, settings.GEMINI_MAX_RETRIES)
    for attempt in range(max_retries + 1):
        try:
            return request()
        except Exception as exc:
            if not _is_retryable_error(exc) or attempt >= max_retries:
                raise
            delay = _retry_delay(attempt)
            logger.warning(
                'Temporary Gemini %s failure: model=%s attempt=%s/%s '
                'error_type=%s retry_in_seconds=%s',
                operation,
                model,
                attempt + 1,
                max_retries + 1,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)


@lru_cache(maxsize=1)
def _get_gemini_client(api_key: str, timeout_ms: int):
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def _request_config(system_prompt: str, max_output_tokens: int | None):
    from google.genai import types

    options = {
        'system_instruction': system_prompt,
        'response_mime_type': 'application/json',
        'temperature': 0.5,
    }
    if max_output_tokens is not None:
        options['max_output_tokens'] = max_output_tokens
    return types.GenerateContentConfig(**options)


def _configured_client():
    api_key = (settings.GEMINI_API_KEY or '').strip()
    if not api_key:
        logger.error(
            'Gemini request not started: API key detected=False, model=%s. '
            'Set GEMINI_API_KEY in the backend env file.',
            settings.GEMINI_MODEL,
        )
        raise AIUnavailableError(
            'Gemini API key is missing. Set GEMINI_API_KEY in the backend env file.'
        )
    return _get_gemini_client(api_key, settings.GEMINI_REQUEST_TIMEOUT_MS)


def chat_json(
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int | None = None,
) -> dict:
    """
    Send a prompt pair to the configured model and parse the JSON response.
    Raises AIUnavailableError when the backend is not configured or the API
    call returns an unusable result.
    """
    started = time.perf_counter()
    model = settings.GEMINI_MODEL
    try:
        client = _configured_client()
        config = _request_config(system_prompt, max_output_tokens)
        logger.info('Sending Gemini generate_content request: model=%s', model)
        try:
            response = _call_with_retries(
                lambda: client.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    config=config,
                ),
                model=model,
                operation='generate_content',
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
            response = _call_with_retries(
                lambda: client.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    config=config,
                ),
                model=model,
                operation='generate_content',
            )
        raw = response.text
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if raw is None or not str(raw).strip():
            raise AIUnavailableError('Gemini returned an empty response.')
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise AIUnavailableError('Gemini returned a non-object JSON payload.')
        logger.info(
            'Gemini response parsed: model=%s elapsed_ms=%s output_chars=%s',
            model, elapsed_ms, len(raw),
        )
        return parsed
    except AIUnavailableError as exc:
        logger.error(
            'Gemini request unavailable: model=%s elapsed_ms=%s error_type=%s',
            model,
            round((time.perf_counter() - started) * 1000),
            type(exc).__name__,
        )
        raise
    except Exception as exc:  # noqa: BLE001 - any provider/parsing failure funnels here
        if _is_invalid_api_key_error(exc):
            logger.error('Gemini rejected configured API key: reason=API_KEY_INVALID')
            raise AIUnavailableError(_invalid_api_key_message()) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if isinstance(exc, httpx.TimeoutException):
            timeout_seconds = settings.GEMINI_REQUEST_TIMEOUT_MS / 1000
            message = (
                f'Gemini request timed out after {timeout_seconds:g} seconds. '
                'Try again; longer notes or quizzes may take more time.'
            )
            logger.error('Gemini request timed out: model=%s elapsed_ms=%s', model, elapsed_ms)
            raise AIUnavailableError(message) from exc
        logger.error(
            'Gemini request failed: elapsed_ms=%s %s',
            elapsed_ms,
            _safe_error_details(exc),
        )
        raise AIUnavailableError(f'Gemini request failed: {_safe_error_details(exc)}') from exc


def stream_json(
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int | None = None,
):
    """Yield response text chunks and return the validated JSON object at completion."""
    model = settings.GEMINI_MODEL
    started = time.perf_counter()
    first_chunk_ms = None
    emitted_text = False
    output = []
    try:
        client = _configured_client()
        config = _request_config(system_prompt, max_output_tokens)
        fallback_model = getattr(settings, 'GEMINI_FALLBACK_MODEL', '')
        max_retries = max(0, settings.GEMINI_MAX_RETRIES)
        logger.info('Sending Gemini streaming request: model=%s', model)

        while True:
            retry_fallback = False
            for attempt in range(max_retries + 1):
                try:
                    for response in client.models.generate_content_stream(
                        model=model,
                        contents=user_prompt,
                        config=config,
                    ):
                        text = response.text
                        if text:
                            if first_chunk_ms is None:
                                first_chunk_ms = round((time.perf_counter() - started) * 1000)
                            emitted_text = True
                            output.append(text)
                            yield text
                    retry_fallback = False
                    break
                except Exception as exc:
                    if emitted_text:
                        raise
                    if _is_retryable_error(exc) and attempt < max_retries:
                        delay = _retry_delay(attempt)
                        logger.warning(
                            'Temporary Gemini streaming failure: model=%s attempt=%s/%s '
                            'error_type=%s retry_in_seconds=%s',
                            model,
                            attempt + 1,
                            max_retries + 1,
                            type(exc).__name__,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                    if (
                        _is_capacity_error(exc)
                        and fallback_model
                        and fallback_model != model
                    ):
                        logger.warning(
                            'Gemini model %s is temporarily unavailable; retrying with fallback model=%s',
                            model,
                            fallback_model,
                        )
                        model = fallback_model
                        retry_fallback = True
                        break
                    raise
            if retry_fallback:
                continue
            break

        raw = ''.join(output)
        if not raw.strip():
            raise AIUnavailableError('Gemini returned an empty response.')
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise AIUnavailableError('Gemini returned a non-object JSON payload.')
        logger.info(
            'Gemini stream completed: model=%s first_chunk_ms=%s elapsed_ms=%s output_chars=%s',
            model,
            first_chunk_ms,
            round((time.perf_counter() - started) * 1000),
            len(raw),
        )
        return parsed
    except AIUnavailableError as exc:
        logger.error(
            'Gemini streaming request unavailable: model=%s elapsed_ms=%s error_type=%s',
            model,
            round((time.perf_counter() - started) * 1000),
            type(exc).__name__,
        )
        raise
    except Exception as exc:  # noqa: BLE001 - provider/parsing failures share one safe error path
        if _is_invalid_api_key_error(exc):
            logger.error('Gemini streaming request rejected configured API key: reason=API_KEY_INVALID')
            raise AIUnavailableError(_invalid_api_key_message()) from exc
        if isinstance(exc, httpx.TimeoutException):
            timeout_seconds = settings.GEMINI_REQUEST_TIMEOUT_MS / 1000
            message = (
                f'Gemini streaming request timed out after {timeout_seconds:g} seconds. '
                'The notes were not saved. Please retry.'
            )
            logger.error(
                'Gemini streaming request timed out: model=%s elapsed_ms=%s',
                model,
                round((time.perf_counter() - started) * 1000),
            )
            raise AIUnavailableError(message) from exc
        logger.error('Gemini streaming request failed: %s', _safe_error_details(exc))
        raise AIUnavailableError(f'Gemini request failed: {_safe_error_details(exc)}') from exc
