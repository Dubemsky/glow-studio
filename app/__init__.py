import logging
import os

from flask import Flask

from config import Config

from . import studio
from .db import init_db
from .timeutils import fmt_euros


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    logging.basicConfig(level=logging.INFO)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    init_db(app)

    from .routes import admin, public

    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)

    from . import notify, payments

    @app.context_processor
    def inject_globals():
        return {
            "studio": studio,
            "euros": fmt_euros,
            "demo_payments": not payments.enabled(),
            "calendar_url": notify.calendar_url,
        }

    from .seed import register_seed

    register_seed(app)

    if app.config["SCHEDULER_ENABLED"] and not app.testing and _is_main_process(app):
        from .scheduler import start_scheduler

        start_scheduler(app)

    return app


def _is_main_process(app):
    # Flask's debug reloader runs the app twice; only start background jobs once.
    return not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"
