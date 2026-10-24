import requests
from flask import current_app, render_template

from . import studio
from .timeutils import fmt_datetime, fmt_euros, site_url

outbox = []


def _cancel_url(booking):
    return f"{site_url()}/cancel/{booking['cancel_token']}"


def calendar_url(booking):
    fmt = "%Y%m%dT%H%M%SZ"
    params = {
        "action": "TEMPLATE",
        "text": f"{booking['service_name']} at {studio.NAME}",
        "dates": f"{booking['start'].strftime(fmt)}/{booking['end'].strftime(fmt)}",
        "location": studio.ADDRESS,
    }
    return requests.Request("GET", "https://calendar.google.com/calendar/render", params=params).prepare().url


def _context(booking):
    return {
        "booking": booking,
        "studio": studio,
        "when": fmt_datetime(booking["start"]),
        "deposit": fmt_euros(booking["deposit_cents"]),
        "balance": fmt_euros(booking["price_cents"] - booking["deposit_cents"]),
        "cancel_url": _cancel_url(booking),
        "calendar_url": calendar_url(booking),
    }


def send_email(to, subject, template, **context):
    html = render_template(f"emails/{template}.html", **context)
    outbox.append({"to": to, "subject": subject})
    key = current_app.config["RESEND_API_KEY"]
    if not key:
        current_app.logger.info("Email (demo) to %s: %s", to, subject)
        return
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": current_app.config["EMAIL_FROM"], "to": [to], "subject": subject, "html": html},
            timeout=10,
        ).raise_for_status()
    except requests.RequestException:
        current_app.logger.exception("Email to %s failed", to)


def email_owner(subject, headline, message, booking):
    owner = current_app.config["OWNER_EMAIL"]
    if not owner:
        current_app.logger.info("Owner alert (no OWNER_EMAIL set): %s", subject)
        return
    send_email(
        owner,
        subject,
        "owner_alert",
        headline=headline,
        message=message,
        dashboard_url=f"{site_url()}/admin",
        **_context(booking),
    )


def booking_confirmed(booking):
    ctx = _context(booking)
    send_email(booking["email"], f"You're booked: {booking['service_name']}, {ctx['when']}", "confirmed", **ctx)
    email_owner(
        f"New booking: {booking['name']}, {booking['service_name']}, {ctx['when']}",
        "New booking",
        f"{booking['name']} ({booking['email']}) booked and paid the {ctx['deposit']} deposit.",
        booking,
    )


def booking_cancelled(booking, by_admin):
    ctx = _context(booking)
    send_email(booking["email"], f"Booking cancelled: {booking['service_name']}, {ctx['when']}", "cancelled", by_admin=by_admin, **ctx)
    if booking.get("refunded"):
        refund = f"The {ctx['deposit']} deposit was refunded."
    elif booking.get("refund_failed"):
        refund = f"The refund failed. Please refund the {ctx['deposit']} deposit manually in Stripe."
    else:
        refund = f"Cancelled within {studio.FREE_CANCEL_HOURS} hours, so the {ctx['deposit']} deposit was kept."
    who = "You cancelled" if by_admin else f"{booking['name']} cancelled"
    email_owner(
        f"Cancelled: {booking['name']}, {booking['service_name']}, {ctx['when']}",
        "Booking cancelled",
        f"{who} this booking. {refund} The time is free again.",
        booking,
    )


def reminder(booking):
    ctx = _context(booking)
    send_email(booking["email"], f"Reminder: {booking['service_name']}, {ctx['when']}", "reminder", **ctx)


def bookings_link(email, url):
    send_email(email, f"Your bookings at {studio.NAME}", "my_bookings", url=url, studio=studio)


def refund_failed(booking):
    ctx = _context(booking)
    email_owner(
        f"Action needed: refund failed for {booking['name']}",
        "Refund failed",
        f"Stripe couldn't refund {booking['name']}'s {ctx['deposit']} deposit ({booking['email']}). "
        "Please refund it manually in your Stripe dashboard.",
        booking,
    )
