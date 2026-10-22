from datetime import datetime, timezone

from flask import current_app, has_request_context, request

from .studio import TIMEZONE


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt_utc):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(TIMEZONE)


def to_utc(dt_local):
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def fmt_datetime(dt_utc):
    return to_local(dt_utc).strftime("%A %-d %B, %H:%M")


def fmt_time(dt_utc):
    return to_local(dt_utc).strftime("%H:%M")


def fmt_euros(cents):
    return f"€{cents / 100:,.0f}" if cents % 100 == 0 else f"€{cents / 100:,.2f}"


def site_url():
    if current_app.config["BASE_URL"]:
        return current_app.config["BASE_URL"]
    if has_request_context():
        return request.url_root.rstrip("/")
    return "http://localhost:5001"
