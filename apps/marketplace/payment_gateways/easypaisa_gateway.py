"""Easypaisa adapter boundary.

The merchant portal provides product-specific hosted checkout guides and credentials.
No customer redirect or unsigned return parameters are accepted as payment evidence.
"""
from django.conf import settings

from .base import GatewayUnavailable


def is_configured():
    return bool(settings.PAYMENT_EASYPAISA_STORE_ID and settings.PAYMENT_EASYPAISA_HASH_KEY)


def create_checkout(payment, request):
    raise GatewayUnavailable(
        'Easypaisa is disabled until merchant approval and provider-issued server-side payment verification/webhook details are configured.'
    )


def verify_webhook(request):
    raise GatewayUnavailable(
        'Easypaisa payment notifications cannot be verified until the merchant integration contract is configured.'
    )
