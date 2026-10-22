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
    return render_template("admin/dashboard.html", **bookings.dashboard_stats())


@bp.post("/bookings/<booking_id>/cancel")
@login_required
def cancel(booking_id):
    booking = bookings.get_booking(booking_id) or abort(404)
    if bookings.cancel(booking, by_admin=True):
        flash(f"Cancelled {booking['name']}'s booking and refunded the deposit.")
    return redirect(url_for("admin.dashboard"))
