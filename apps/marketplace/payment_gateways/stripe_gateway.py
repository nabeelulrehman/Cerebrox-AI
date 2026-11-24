from decimal import Decimal

from django.conf import settings
from django.urls import reverse

from .base import GatewayUnavailable


def create_checkout(payment, request):
    if not settings.PAYMENT_STRIPE_SECRET_KEY or not settings.PAYMENT_STRIPE_WEBHOOK_SECRET:
        raise GatewayUnavailable('Stripe is not configured for verified payments.')
    try:
        import stripe
    except ImportError as exc:
        raise GatewayUnavailable('Stripe support is not installed on this server.') from exc
    stripe.api_key = settings.PAYMENT_STRIPE_SECRET_KEY
    amount_minor = int((Decimal(payment.amount) * 100).quantize(Decimal('1')))
    base = settings.SITE_URL or request.build_absolute_uri('/')[:-1]
    session = stripe.checkout.Session.create(
        mode='payment',
        payment_method_types=['card'],
        client_reference_id=payment.reference,
        customer_email=payment.student.email or None,
        line_items=[{'price_data': {
            'currency': 'pkr',
            'product_data': {'name': payment.session.title, 'description': 'Live online class booking'},
            'unit_amount': amount_minor,
        }, 'quantity': 1}],
        metadata={'payment_reference': payment.reference, 'session_id': str(payment.session_id)},
        success_url=base + reverse('market:payment_result', args=[payment.reference]),
        cancel_url=base + reverse('market:payment_result', args=[payment.reference]),
        idempotency_key=f'cerebrox-{payment.reference}',
    )
    payment.provider_session_id = session.id
    payment.checkout_url = session.url or ''
    payment.save(update_fields=['provider_session_id', 'checkout_url'])
    return session.url


def parse_verified_event(request):
    try:
        import stripe
        event = stripe.Webhook.construct_event(
            request.body,
            request.headers.get('Stripe-Signature', ''),
            settings.PAYMENT_STRIPE_WEBHOOK_SECRET,
        )
        expected_live = settings.PAYMENT_STRIPE_MODE == 'live'
        if bool(event.get('livemode')) != expected_live:
            raise GatewayUnavailable('Stripe webhook environment does not match configured payment mode.')
        return event
    except ImportError as exc:
        raise GatewayUnavailable('Stripe support is not installed.') from exc
    except Exception as exc:
        raise GatewayUnavailable('Invalid Stripe webhook signature or payload.') from exc
