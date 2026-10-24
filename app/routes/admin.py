import hmac
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .. import bookings

bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password, current_app.config["ADMIN_PASSWORD"]):
            session["admin"] = True
            return redirect(url_for("admin.dashboard"))
        flash("Wrong password.")
    return render_template("admin/login.html")


@bp.post("/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("public.index"))


@bp.get("/")
@login_required
def dashboard():
    tab = request.args.get("tab", "upcoming")
    if tab not in bookings.ADMIN_TABS:
        tab = "upcoming"
    search = request.args.get("q", "").strip()[:80]
    service_id = request.args.get("service", "")
    return render_template(
        "admin/bookings.html",
        tab=tab,
        search=search,
        service_id=service_id,
        items=bookings.admin_bookings(tab, search, service_id),
        counts=bookings.admin_counts(),
    )


@bp.get("/overview")
@login_required
def overview():
    return render_template("admin/overview.html", **bookings.overview())


@bp.post("/bookings/<booking_id>/cancel")
@login_required
def cancel(booking_id):
    booking = bookings.get_booking(booking_id) or abort(404)
    updated = bookings.cancel(booking, by_admin=True)
    if updated and updated.get("refund_failed"):
        flash(f"Cancelled {booking['name']}'s booking, but the refund failed. Refund it manually in Stripe.")
    elif updated:
        flash(f"Cancelled {booking['name']}'s booking and refunded the deposit.")
    return redirect(request.referrer or url_for("admin.dashboard"))
