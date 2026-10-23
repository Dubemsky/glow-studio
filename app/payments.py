import json
import time

import stripe
from flask import current_app, url_for

from . import studio
from .timeutils import fmt_datetime, site_url


def enabled():
    return bool(current_app.config["STRIPE_SECRET_KEY"])


def _client():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout(booking):
    base = site_url()
    session = _client().checkout.Session.create(
        mode="payment",
        customer_email=booking["email"],
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": booking["deposit_cents"],
                    "product_data": {
                        "name": f"Deposit: {booking['service_name']}",
                        "description": fmt_datetime(booking["start"]),
                    },
                },
            }
        ],
        metadata={"booking_id": str(booking["_id"])},
        # Stripe's minimum session lifetime, matched by our own slot hold.
        expires_at=int(time.time()) + studio.HOLD_MINUTES * 60,
        success_url=base + url_for("public.success", token=booking["cancel_token"]),
        cancel_url=base + url_for("public.checkout_cancelled", token=booking["cancel_token"]),
    )
    return session


def parse_webhook(payload, signature):
    # Rejects anything not signed by Stripe, so nobody can fake a "payment succeeded" call.
    stripe.WebhookSignature.verify_header(
        payload.decode(),
        signature,
        current_app.config["STRIPE_WEBHOOK_SECRET"],
        tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
    )
    return json.loads(payload)


def paid_intent(session_id):
    """Ask Stripe directly whether a checkout was paid. Returns the payment intent, or None."""
    if not session_id or not enabled():
        return None
    session = _client().checkout.Session.retrieve(session_id)
    return session.payment_intent if session.payment_status == "paid" else None


def refund(payment_intent):
    if not payment_intent or not enabled() or payment_intent.startswith("demo_"):
        return
    _client().Refund.create(payment_intent=payment_intent)
