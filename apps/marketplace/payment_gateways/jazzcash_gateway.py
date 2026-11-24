"""JazzCash adapter boundary.

JazzCash sends merchants through several products and merchant-specific status APIs.
This project intentionally fails closed until the approved server-side status inquiry
endpoint and production contract are supplied; a browser return is never proof of payment.
"""
from django.conf import settings

from .base import GatewayUnavailable


def is_configured():
    return bool(settings.PAYMENT_JAZZCASH_MERCHANT_ID and settings.PAYMENT_JAZZCASH_PASSWORD
                and settings.PAYMENT_JAZZCASH_INTEGRITY_SALT)


def create_checkout(payment, request):
    raise GatewayUnavailable(
        'JazzCash is disabled until merchant approval and the provider-issued server status inquiry contract are configured.'
    )


def verify_webhook(request):
    raise GatewayUnavailable(
        'JazzCash payment notifications cannot be verified until the merchant-specific inquiry API is configured.'
    )
