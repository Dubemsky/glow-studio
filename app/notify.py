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


def send_slack(text):
    url = current_app.config["SLACK_WEBHOOK_URL"]
    if not url:
        current_app.logger.info("Slack (demo): %s", text)
        return
    try:
        requests.post(url, json={"text": text}, timeout=10).raise_for_status()
    except requests.RequestException:
        current_app.logger.exception("Slack alert failed")


def booking_confirmed(booking):
    ctx = _context(booking)
    send_email(booking["email"], f"You're booked: {booking['service_name']}, {ctx['when']}", "confirmed", **ctx)
    send_slack(f":sparkles: New booking: *{booking['name']}*, {booking['service_name']}, {ctx['when']} ({ctx['deposit']} deposit paid)")


def booking_cancelled(booking, by_admin):
    ctx = _context(booking)
    send_email(booking["email"], f"Booking cancelled: {booking['service_name']}, {ctx['when']}", "cancelled", by_admin=by_admin, **ctx)
    if booking.get("refunded"):
        refund = "deposit refunded"
    elif booking.get("refund_failed"):
        refund = "REFUND FAILED, refund manually in Stripe"
    else:
        refund = "deposit kept"
    who = "by the studio" if by_admin else "by the customer"
    send_slack(f":x: Cancelled {who}: *{booking['name']}*, {booking['service_name']}, {ctx['when']} ({refund})")


def reminder(booking):
    ctx = _context(booking)
    send_email(booking["email"], f"Reminder: {booking['service_name']}, {ctx['when']}", "reminder", **ctx)


def bookings_link(email, url):
    send_email(email, f"Your bookings at {studio.NAME}", "my_bookings", url=url, studio=studio)


def refund_failed(booking):
    ctx = _context(booking)
    send_slack(
        f":warning: Refund failed for *{booking['name']}* ({booking['email']}), "
        f"{booking['service_name']}, {ctx['when']}. Please refund {ctx['deposit']} manually in Stripe."
    )
