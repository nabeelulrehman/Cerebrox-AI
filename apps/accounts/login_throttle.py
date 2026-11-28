"""Database-backed, privacy-preserving throttling for password login endpoints."""
import hashlib
import hmac
import ipaddress
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginRateLimitBucket

logger = logging.getLogger(__name__)
ATTEMPT_LIMIT = 5
WINDOW = timedelta(minutes=15)


def _bucket_key(kind, value):
    normalized = value.strip().casefold() if kind == 'account' else value
    digest = hmac.new(
        settings.SECRET_KEY.encode(),
        f'login-rate-limit:{kind}:{normalized}'.encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest


def _client_ip(request):
    # Vercel overwrites these headers with the public client IP; use its canonical
    # header because REMOTE_ADDR may be the shared serverless gateway address.
    if settings.IS_VERCEL:
        candidate = (
            request.META.get('HTTP_X_VERCEL_FORWARDED_FOR')
            or request.META.get('HTTP_X_FORWARDED_FOR', '')
        )
    else:
        # Do not trust arbitrary X-Forwarded-For input outside the trusted Vercel edge.
        candidate = request.META.get('REMOTE_ADDR', '')
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return 'unknown'


def consume_login_attempt(request, identifier):
    """Count attempts by normalized account and source IP without revealing account existence."""
    now = timezone.now()
    keys = (
        _bucket_key('account', identifier or ''),
        _bucket_key('ip', _client_ip(request)),
    )
    blocked = False
    retry_after = 0
    alerts = []

    with transaction.atomic():
        for key in keys:
            bucket, _ = LoginRateLimitBucket.objects.get_or_create(
                key=key,
                defaults={'window_started_at': now, 'attempts': 0},
            )
            bucket = LoginRateLimitBucket.objects.select_for_update().get(pk=bucket.pk)
            if now - bucket.window_started_at >= WINDOW:
                bucket.window_started_at = now
                bucket.attempts = 0
                bucket.alerted = False
            if bucket.attempts >= ATTEMPT_LIMIT:
                blocked = True
                retry_after = max(
                    retry_after,
                    math.ceil((bucket.window_started_at + WINDOW - now).total_seconds()),
                )
                if not bucket.alerted:
                    bucket.alerted = True
                    alerts.append(key[:12])
            else:
                bucket.attempts += 1
            bucket.save(update_fields=['window_started_at', 'attempts', 'alerted'])

    if alerts:
        logger.warning(
            'login_throttle_triggered buckets=%s count=%s',
            ','.join(alerts), len(alerts),
        )
    if not blocked:
        # Keep only active-window identifiers; the indexed timestamp bounds table growth.
        LoginRateLimitBucket.objects.filter(window_started_at__lt=now - WINDOW).delete()
    return blocked, max(1, retry_after)


def normalize_login_identifier(value):
    """Normalize username/email consistently before generating account buckets."""
    return (value or '').strip().casefold()
