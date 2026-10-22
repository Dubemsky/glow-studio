from datetime import time
from zoneinfo import ZoneInfo

NAME = "Glow Studio"
ADDRESS = "14 Drury Street, Dublin 2"
TIMEZONE = ZoneInfo("Europe/Dublin")

OPEN_DAYS = {1, 2, 3, 4, 5}  # Tue–Sat
OPENS = time(9, 0)
CLOSES = time(18, 0)

SLOT_MINUTES = 30
MIN_NOTICE_MINUTES = 60
BOOKING_WINDOW_DAYS = 21
HOLD_MINUTES = 30
FREE_CANCEL_HOURS = 24

DEPOSIT_CENTS = 1000

SERVICES = [
    {
        "id": "manicure",
        "name": "Manicure",
        "description": "Shape, cuticle care and a polish of your choice.",
        "minutes": 30,
        "price_cents": 3000,
    },
    {
        "id": "facial",
        "name": "Facial",
        "description": "Deep cleanse, exfoliation and a hydrating mask.",
        "minutes": 60,
        "price_cents": 6500,
    },
    {
        "id": "massage",
        "name": "Full Body Massage",
        "description": "A slow, relaxing massage with warm oils.",
        "minutes": 90,
        "price_cents": 9000,
    },
]


def get_service(service_id):
    return next((s for s in SERVICES if s["id"] == service_id), None)
