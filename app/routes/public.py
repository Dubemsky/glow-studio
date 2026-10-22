import re
from datetime import date, datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from .. import bookings, payments, studio
from ..db import get_db
from ..timeutils import to_local, utcnow

bp = Blueprint("public", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _service_or_404(service_id):
    service = studio.get_service(service_id)
    if not service:
        abort(404)
    return service


def _parse_day(value, days, service):
    try:
        day = date.fromisoformat(value)
        if day in days:
            return day
    except (TypeError, ValueError):
        pass
    return next((d for d in days if bookings.available_slots(service, d)), days[0] if days else None)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/book/<service_id>")
def book(service_id):
    service = _service_or_404(service_id)
    days = bookings.open_days()
    day = _parse_day(request.args.get("date"), days, service)
    slots = bookings.available_slots(service, day) if day else []

    chosen = None
    time_arg = request.args.get("time")
    if time_arg:
        chosen = next((s for s in slots if to_local(s).strftime("%H:%M") == time_arg), None)

    return render_template("book.html", service=service, days=days, day=day, slots=slots, chosen=chosen)


@bp.post("/book/<service_id>")
def create_booking(service_id):
    service = _service_or_404(service_id)
    name = request.form.get("name", "").strip()[:80]
    email = request.form.get("email", "").strip().lower()[:120]
    day_arg, time_arg = request.form.get("date", ""), request.form.get("time", "")

    back = url_for("public.book", service_id=service_id, date=day_arg, time=time_arg)
    if not name or not EMAIL_RE.match(email):
        flash("Please enter your name and a valid email address.")
        return redirect(back)

    try:
        day = date.fromisoformat(day_arg)
        start = next(s for s in bookings.available_slots(service, day) if to_local(s).strftime("%H:%M") == time_arg)
    except (ValueError, StopIteration):
        flash("Sorry, that time is no longer available. Please pick another.")
        return redirect(url_for("public.book", service_id=service_id, date=day_arg))

    booking = bookings.create_pending(service, start, name, email)
    if not booking:
        flash("Someone just booked that time. Please pick another.")
        return redirect(url_for("public.book", service_id=service_id, date=day_arg))

    if not payments.enabled():
        return redirect(url_for("public.demo_pay", token=booking["cancel_token"]))

    try:
        session = payments.create_checkout(booking)
    except Exception:
        current_app.logger.exception("Stripe checkout failed")
        bookings.expire(booking["_id"])
        flash("We couldn't start the payment. Please try again.")
        return redirect(back)

    get_db().bookings.update_one(
        {"_id": booking["_id"]}, {"$set": {"stripe_session_id": session.id}}
    )
    return redirect(session.url, code=303)


@bp.route("/demo-pay/<token>", methods=["GET", "POST"])
def demo_pay(token):
    if payments.enabled():
        abort(404)
    booking = bookings.get_by_token(token) or abort(404)
    if booking["status"] != "pending":
        return redirect(url_for("public.success", token=token))
    if request.method == "POST":
        bookings.confirm(booking["_id"], payment_intent=f"demo_{booking['_id']}")
        return redirect(url_for("public.success", token=token))
    return render_template("demo_pay.html", booking=booking)


@bp.get("/booking/<token>")
def success(token):
    booking = bookings.get_by_token(token) or abort(404)
    return render_template("success.html", booking=booking)


@bp.get("/booking/<token>/checkout-cancelled")
def checkout_cancelled(token):
    booking = bookings.get_by_token(token) or abort(404)
    bookings.expire(booking["_id"])
    flash("Payment cancelled, so your time wasn't booked.")
    day = to_local(booking["start"]).date().isoformat()
    return redirect(url_for("public.book", service_id=booking["service_id"], date=day))


@bp.route("/cancel/<token>", methods=["GET", "POST"])
def cancel(token):
    booking = bookings.get_by_token(token) or abort(404)
    if request.method == "POST" and booking["status"] == "confirmed":
        if booking["start"] <= utcnow():
            flash("This appointment has already started.")
        else:
            booking = bookings.cancel(booking) or booking
    return render_template(
        "cancel.html",
        booking=booking,
        refundable=bookings.refund_eligible(booking),
        past=booking["start"] <= utcnow(),
    )


@bp.post("/stripe/webhook")
def stripe_webhook():
    try:
        event = payments.parse_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""))
    except Exception:
        abort(400)

    session = event["data"]["object"]
    booking = bookings.get_booking(session.get("metadata", {}).get("booking_id"))
    if not booking:
        return "", 200

    if event["type"] == "checkout.session.completed" and session.get("payment_status") == "paid":
        bookings.confirm(booking["_id"], payment_intent=session.get("payment_intent"))
    elif event["type"] == "checkout.session.expired":
        bookings.expire(booking["_id"])
    return "", 200


@bp.app_template_filter("local")
def local_filter(dt, fmt="%A %-d %B, %H:%M"):
    return to_local(dt).strftime(fmt) if isinstance(dt, datetime) else ""
