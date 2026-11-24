from django.conf import settings
from apps.marketplace.models import PaymentMethodConfiguration, Payment


class GatewayUnavailable(Exception):
    pass


def configuration():
    return PaymentMethodConfiguration.objects.filter(pk=1).first()


def payment_methods():
    config = configuration()
    methods = []
    for code, label in Payment.Method.choices:
        enabled = False
        reason = 'Requires merchant credentials and provider approval.'
        if code == Payment.Method.BANK_TRANSFER:
            ready = bool(config and config.bank_transfer_enabled and (
                config.bank_iban or config.bank_raast_id
            ) and config.bank_account_title)
            enabled = ready
            reason = '' if ready else 'Admin must enable this method and configure the account title and Raast ID or IBAN.'
        elif code == Payment.Method.STRIPE:
            secret = settings.PAYMENT_STRIPE_SECRET_KEY
            expected_prefix = {'live': 'sk_live_', 'test': 'sk_test_'}.get(settings.PAYMENT_STRIPE_MODE)
            ready = bool(config and config.stripe_enabled and expected_prefix
                         and secret.startswith(expected_prefix)
                         and settings.PAYMENT_STRIPE_WEBHOOK_SECRET)
            enabled = ready
            reason = '' if ready else ('Requires admin enablement, an approved Stripe account, a matching '
                                       'test/live secret key, and webhook signing secret.')
        elif code == Payment.Method.JAZZCASH:
            ready = bool(config and config.jazzcash_enabled and settings.PAYMENT_JAZZCASH_MERCHANT_ID
                         and settings.PAYMENT_JAZZCASH_PASSWORD and settings.PAYMENT_JAZZCASH_INTEGRITY_SALT)
            # Do not expose the method until a provider-approved server-side inquiry contract is configured.
            enabled = False
            reason = ('Requires admin enablement, approved merchant credentials, and a provider-issued status inquiry '
                      'integration contract. No inquiry endpoint is configured, so it remains disabled.')
        elif code == Payment.Method.EASYPAISA:
            ready = bool(config and config.easypaisa_enabled and settings.PAYMENT_EASYPAISA_STORE_ID
                         and settings.PAYMENT_EASYPAISA_HASH_KEY)
            enabled = False
            reason = ('Requires admin enablement, approved merchant credentials, and provider-issued server-side '
                      'transaction verification/webhook documentation. It remains disabled until that is configured.')
        methods.append({'code': code, 'label': label, 'enabled': enabled, 'reason': reason})
    return methods


def method_is_enabled(code):
    return any(item['code'] == code and item['enabled'] for item in payment_methods())
