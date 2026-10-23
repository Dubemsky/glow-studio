import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest

from app import bookings, create_app, notify, studio
from app.db import get_db
from app.timeutils import to_local, utcnow

WEBHOOK_SECRET = "whsec_test"


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "MONGO_URI": "", "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET})
    with app.app_context():
        get_db().bookings.delete_many({})
        get_db().blocks.delete_many({})
        notify.outbox.clear()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def first_slot(service_id="facial", day_index=2):
    service = studio.get_service(service_id)
    day = bookings.open_days()[day_index]
    return service, day, bookings.available_slots(service, day)[0]


def book_via_form(client, service, day, start):
    return client.post(
        f"/book/{service['id']}",
        data={"date": day.isoformat(), "time": to_local(start).strftime("%H:%M"), "name": "Aoife Byrne", "email": "aoife@example.com"},
    )


def test_pages_load(client):
    assert client.get("/").status_code == 200
    assert client.get("/book/massage").status_code == 200
    assert client.get("/book/nope").status_code == 404


def test_demo_booking_flow(client):
    service, day, start = first_slot()
    response = book_via_form(client, service, day, start)
    assert "/demo-pay/" in response.location

    client.post(response.location)
    booking = get_db().bookings.find_one()
    assert booking["status"] == "confirmed"
    assert start not in bookings.available_slots(service, day)
    assert [m["to"] for m in notify.outbox] == ["aoife@example.com"]


def test_no_double_booking(app):
    facial, day, start = first_slot("facial")
    massage = studio.get_service("massage")

    assert bookings.create_pending(facial, start, "A", "a@example.com")
    assert bookings.create_pending(facial, start, "B", "b@example.com") is None
    assert bookings.create_pending(massage, start - timedelta(minutes=30), "C", "c@example.com") is None
    assert get_db().blocks.count_documents({}) == 2


def test_unpaid_hold_expires(app):
    service, day, start = first_slot()
    booking = bookings.create_pending(service, start, "A", "a@example.com")
    get_db().bookings.update_one({"_id": booking["_id"]}, {"$set": {"created_at": utcnow() - timedelta(minutes=31)}})

    bookings.expire_stale()
    assert get_db().bookings.find_one()["status"] == "expired"
    assert start in bookings.available_slots(service, day)


def test_cancel_refund_rules(app):
    service, day, start = first_slot()
    early = bookings.confirm(bookings.create_pending(service, start, "A", "a@example.com")["_id"], "demo_1")
    assert bookings.cancel(early)["refunded"] is True

    late = bookings.confirm(bookings.create_pending(service, start, "B", "b@example.com")["_id"], "demo_2")
    get_db().bookings.update_one({"_id": late["_id"]}, {"$set": {"start": utcnow() + timedelta(hours=3)}})
    late = bookings.get_booking(late["_id"])
    assert bookings.cancel(late)["refunded"] is False


def signed(payload):
    timestamp = int(time.time())
    signature = hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"}


def test_webhook(client):
    service, day, start = first_slot()
    booking = bookings.create_pending(service, start, "A", "a@example.com")
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"payment_status": "paid", "payment_intent": "pi_123", "metadata": {"booking_id": str(booking["_id"])}}},
    })

    assert client.post("/stripe/webhook", data=payload, headers={"Stripe-Signature": "t=1,v1=fake"}).status_code == 400
    assert get_db().bookings.find_one()["status"] == "pending"

    assert client.post("/stripe/webhook", data=payload, headers=signed(payload)).status_code == 200
    assert get_db().bookings.find_one()["status"] == "confirmed"


def test_reminder_sent_once(app):
    service, day, start = first_slot()
    booking = bookings.confirm(bookings.create_pending(service, start, "A", "a@example.com")["_id"], "demo_1")
    get_db().bookings.update_one({"_id": booking["_id"]}, {"$set": {"start": utcnow() + timedelta(hours=20), "reminder_sent": False}})
    notify.outbox.clear()

    bookings.send_reminders()
    bookings.send_reminders()
    assert len(notify.outbox) == 1


def test_admin(client, app):
    assert client.get("/admin/").status_code == 302
    client.post("/admin/login", data={"password": app.config["ADMIN_PASSWORD"]})
    assert client.get("/admin/").status_code == 200


def test_find_my_bookings(client):
    service, day, start = first_slot()
    bookings.confirm(bookings.create_pending(service, start, "A", "a@example.com")["_id"], "demo_1")

    page = client.post("/my-bookings", data={"email": "a@example.com"}).get_data(as_text=True)
    link = page.split('href="http://localhost')[1].split('"')[0].split('/', 1)[1]
    assert "Facial" in client.get(link).get_data(as_text=True)

    page = client.post("/my-bookings", data={"email": "nobody@example.com"}).get_data(as_text=True)
    assert "Check your inbox" in page and "Open my bookings" not in page
    assert client.get("/my-bookings/forged-token").status_code == 302


def test_admin_tabs_and_search(client, app):
    service, day, start = first_slot()
    bookings.confirm(bookings.create_pending(service, start, "Aoife Byrne", "a@example.com")["_id"], "demo_1")
    client.post("/admin/login", data={"password": app.config["ADMIN_PASSWORD"]})

    assert "Aoife Byrne" in client.get("/admin/?q=aoife").get_data(as_text=True)
    assert "Aoife Byrne" not in client.get("/admin/?q=ciara").get_data(as_text=True)
    assert "Aoife Byrne" not in client.get("/admin/?service=massage").get_data(as_text=True)
    assert client.get("/admin/overview").status_code == 200


def test_success_page_checks_stripe_when_webhook_is_missing(client, monkeypatch):
    service, day, start = first_slot()
    booking = bookings.create_pending(service, start, "A", "a@example.com")
    get_db().bookings.update_one({"_id": booking["_id"]}, {"$set": {"stripe_session_id": "cs_test_1"}})

    from app import payments
    monkeypatch.setattr(payments, "paid_intent", lambda session_id: "pi_1" if session_id == "cs_test_1" else None)

    page = client.get(f"/booking/{booking['cancel_token']}").get_data(as_text=True)
    assert "You&#39;re booked" in page or "You're booked" in page
    assert get_db().bookings.find_one()["payment_intent"] == "pi_1"
