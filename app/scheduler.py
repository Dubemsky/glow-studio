from apscheduler.schedulers.background import BackgroundScheduler

from . import bookings


def start_scheduler(app):
    def run(job):
        with app.app_context():
            try:
                job()
            except Exception:
                app.logger.exception("Scheduled job %s failed", job.__name__)

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run, "interval", minutes=1, args=[bookings.expire_stale])
    scheduler.add_job(run, "interval", minutes=5, args=[bookings.send_reminders])
    scheduler.start()
    return scheduler
