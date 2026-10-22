import secrets
from datetime import datetime, timedelta

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import notify, payments, studio
from .db import get_db
from .timeutils import to_local, to_utc, utcnow


def block_keys(start, minutes):
    count = minutes // studio.SLOT_MINUTES
    return [
        (start + timedelta(minutes=studio.SLOT_MINUTES * i)).isoformat()
        for i in range(count)
    ]


def reserve_blocks(booking_id, start, minutes):
    # One document per 30-minute block, keyed by its start time. Mongo's unique
    # _id means two customers can never hold the same block, even at the same instant.
    db = get_db()
    for key in block_keys(start, minutes):
        try:
            db.blocks.insert_one({"_id": key, "booking_id": booking_id})
        except DuplicateKeyError:
            release_blocks(booking_id)
            return False
    return True


def release_blocks(booking_id):
    get_db().blocks.delete_many({"booking_id": booking_id})


def open_days(count=10):
    now = to_local(utcnow())
    first = 0 if now.time() < studio.CLOSES else 1
    days = []
    for offset in range(first, studio.BOOKING_WINDOW_DAYS):
        day = now.date() + timedelta(days=offset)
        if day.weekday() in studio.OPEN_DAYS:
            days.append(day)
        if len(days) == count:
            break
    return days


def available_slots(service, day):
    if day.weekday() not in studio.OPEN_DAYS:
        return []

    opens = datetime.combine(day, studio.OPENS, studio.TIMEZONE)
    closes = datetime.combine(day, studio.CLOSES, studio.TIMEZONE)
    earliest = utcnow() + timedelta(minutes=studio.MIN_NOTICE_MINUTES)

    day_start, day_end = to_utc(opens).isoformat(), to_utc(closes).isoformat()
    taken = {
        b["_id"]
        for b in get_db().blocks.find({"_id": {"$gte": day_start, "$lt": day_end}})
    }

    slots = []
    local = opens
    while local + timedelta(minutes=service["minutes"]) <= closes:
        start = to_utc(local)
        if start >= earliest and not taken.intersection(
            block_keys(start, service["minutes"])
        ):
            slots.append(start)
        local += timedelta(minutes=studio.SLOT_MINUTES)
    return slots


def create_pending(service, start, name, email):
    booking_id = ObjectId()
    if not reserve_blocks(booking_id, start, service["minutes"]):
        return None

    booking = {
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
        "status": "pending",
        "cancel_token": secrets.token_urlsafe(24),
        "stripe_session_id": None,
        "payment_intent": None,
        "refunded": False,
        "reminder_sent": False,
        "created_at": utcnow(),
        "confirmed_at": None,
    }
    get_db().bookings.insert_one(booking)
    return booking


def get_booking(booking_id):
    try:
        return get_db().bookings.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        return None


def get_by_token(token):
    return get_db().bookings.find_one({"cancel_token": token})


def confirm(booking_id, payment_intent=None):
    db = get_db()
    booking = db.bookings.find_one({"_id": booking_id})
    if not booking or booking["status"] in ("confirmed", "cancelled"):
        return booking

    if booking["status"] == "expired":
        # Paid after the hold ran out: keep it if the slot is still free, otherwise refund.
        if not reserve_blocks(booking_id, booking["start"], booking["minutes"]):
            payments.refund(payment_intent)
            db.bookings.update_one(
                {"_id": booking_id},
                {"$set": {"payment_intent": payment_intent, "refunded": True}},
            )
            return None

    soon = booking["start"] - utcnow() < timedelta(hours=24)
    booking = db.bookings.find_one_and_update(
        {"_id": booking_id, "status": {"$ne": "confirmed"}},
        {
            "$set": {
                "status": "confirmed",
                "payment_intent": payment_intent,
                "confirmed_at": utcnow(),
                "reminder_sent": soon,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if booking:
        notify.booking_confirmed(booking)
    return booking


def cancel(booking, by_admin=False):
    db = get_db()
    updated = db.bookings.find_one_and_update(
        {"_id": booking["_id"], "status": {"$in": ["pending", "confirmed"]}},
        {"$set": {"status": "cancelled", "cancelled_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return None

    release_blocks(booking["_id"])

    was_paid = booking["status"] == "confirmed"
    if was_paid and (by_admin or refund_eligible(booking)):
        payments.refund(booking["payment_intent"])
        updated = db.bookings.find_one_and_update(
            {"_id": booking["_id"]},
            {"$set": {"refunded": True}},
            return_document=ReturnDocument.AFTER,
        )

    if was_paid:
        notify.booking_cancelled(updated, by_admin)
    return updated


def refund_eligible(booking):
    return booking["start"] - utcnow() >= timedelta(hours=studio.FREE_CANCEL_HOURS)


def expire(booking_id):
    result = get_db().bookings.update_one(
        {"_id": booking_id, "status": "pending"},
        {"$set": {"status": "expired"}},
    )
    if result.modified_count:
        release_blocks(booking_id)


def expire_stale():
    cutoff = utcnow() - timedelta(minutes=studio.HOLD_MINUTES)
    for booking in get_db().bookings.find({"status": "pending", "created_at": {"$lt": cutoff}}):
        expire(booking["_id"])


def send_reminders():
    db = get_db()
    now = utcnow()
    while True:
        # Claim each booking atomically so a reminder is never sent twice.
        booking = db.bookings.find_one_and_update(
            {
                "status": "confirmed",
                "reminder_sent": False,
                "start": {"$gt": now, "$lte": now + timedelta(hours=24)},
            },
            {"$set": {"reminder_sent": True}},
        )
        if not booking:
            break
        notify.reminder(booking)


def dashboard_stats():
    db = get_db()
    now = utcnow()
    local_now = to_local(now)
    today_start = to_utc(local_now.replace(hour=0, minute=0, second=0, microsecond=0))
    week_start = today_start - timedelta(days=local_now.weekday())

    confirmed = {"status": "confirmed"}
    today = list(
        db.bookings.find(
            {**confirmed, "start": {"$gte": today_start, "$lt": today_start + timedelta(days=1)}}
        ).sort("start", 1)
    )
    upcoming = list(db.bookings.find({**confirmed, "start": {"$gte": now}}).sort("start", 1))

    deposits = sum(
        b["deposit_cents"]
        for b in db.bookings.find(
            {"confirmed_at": {"$gte": week_start}, "refunded": False, "status": {"$in": ["confirmed", "cancelled"]}}
        )
    )
    recent_cancellations = list(
        db.bookings.find({"status": "cancelled", "cancelled_at": {"$gte": week_start}}).sort("cancelled_at", -1)
    )

    return {
        "today": today,
        "upcoming": upcoming,
        "deposits_cents": deposits,
        "cancellations": recent_cancellations,
    }
