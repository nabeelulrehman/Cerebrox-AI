# Live session payments

Paid public live-session bookings use the existing `Payment` and `SessionBooking` records. A booking remains `pending_payment` until a verified Stripe webhook or an administrator's bank-transfer review confirms the payment. Class members continue to use the existing free enrollment path.

## Admin setup

Create the single **Payment method configuration** record in Django admin. Enable Bank Transfer only after entering the account title and at least one Raast ID or IBAN. Configure account details and transfer instructions there. Receipt uploads are private and can only be retrieved by an administrator.

Stripe also requires `PAYMENT_STRIPE_SECRET_KEY`, `PAYMENT_STRIPE_WEBHOOK_SECRET`, and `PAYMENT_STRIPE_MODE=test` or `live`. Use a test key with test mode and a live key with live mode. Configure a Stripe webhook at `/market/payments/webhooks/stripe/` for `checkout.session.completed`, `checkout.session.async_payment_succeeded`, and `checkout.session.expired`. Payment completion is based on the signed webhook and matching server-side reference, checkout ID, amount, and currency, never on the return page. Stripe currently does not list Pakistan among the supported business account countries, so activation requires an already approved Stripe account in a supported country; API keys alone do not provide merchant eligibility.

## Gateways awaiting merchant enablement

JazzCash and Easypaisa remain unavailable in checkout until the merchant account is approved and the provider supplies its production credentials and the server-side transaction inquiry/webhook contract for that merchant. The public return/callback alone is not accepted as proof. Set provider secrets only in the server environment; the corresponding admin enable switch is not sufficient to enable an unverified integration.

The gateway credentials and mode variables are listed in `.env.example`. Do not put real values in that file or in source control. Production checkout also requires a public HTTPS `SITE_URL`.
