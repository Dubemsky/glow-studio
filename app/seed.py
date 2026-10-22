import random
import secrets
from datetime import timedelta

import click
from bson import ObjectId

from . import bookings, studio
from .db import get_db
from .timeutils import utcnow

CUSTOMERS = [
    ("Aoife Byrne", "aoife@example.com"),
    ("Ciara Walsh", "ciara@example.com"),
    ("Niamh Kelly", "niamh@example.com"),
    ("Sinéad Murphy", "sinead@example.com"),
    ("Orla Doyle", "orla@example.com"),
    ("Emma Ryan", "emma@example.com"),
    ("Saoirse Quinn", "saoirse@example.com"),
    ("Grace Nolan", "grace@example.com"),
]


def seed_demo(count=10):
    rng = random.Random(7)
    created = 0
    for day in bookings.open_days(5):
        for _ in range(3):
            if created == count:
                return created
            service = rng.choice(studio.SERVICES)
            slots = bookings.available_slots(service, day)
            if not slots:
                continue
            start = rng.choice(slots)
            booking_id = ObjectId()
            if not bookings.reserve_blocks(booking_id, start, service["minutes"]):
                continue
            name, email = rng.choice(CUSTOMERS)
            now = utcnow()
            get_db().bookings.insert_one(
                {
                    "_id": booking_id,
                    "service_id": service["id"],
                    "service_name": service["name"],
                    "minutes": service["minutes"],
                    "price_cents": service["price_cents"],
                    "deposit_cents": studio.DEPOSIT_CENTS,
                    "name": name,
                    "email": email,
                    "start": start,
                    "end": start + timedelta(minutes=service["minutes"]),
                    "status": "confirmed",
                    "cancel_token": secrets.token_urlsafe(24),
                    "stripe_session_id": None,
                    "payment_intent": f"demo_{booking_id}",
                    "refunded": False,
                    "reminder_sent": True,
                    "created_at": now,
                    "confirmed_at": now,
                }
            )
            created += 1
    return created


def register_seed(app):
    @app.cli.command("seed")
    def seed_command():
        """Add sample bookings so the dashboard has something to show."""
        click.echo(f"Added {seed_demo()} demo bookings.")

    if app.config.get("DEMO_DB") and not app.testing:
        with app.app_context():
            if get_db().bookings.count_documents({}) == 0:
                seed_demo()
