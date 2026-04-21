"""
Background scheduler — study timetable notification jobs.
Runs a check every minute; sends in-app notifications at 30, 15, and 5 minutes
before each user's scheduled study session.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from ..database import SessionLocal
from ..models.study import StudySchedule

log = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(
    timezone="UTC",
    job_defaults={"misfire_grace_time": 90},  # tolerate up to 90 s late firing
)


# ── Core job ──────────────────────────────────────────────────────────────────

def _check_study_notifications() -> None:
    """Query for sessions approaching their start time and fire notifications."""
    db = SessionLocal()
    try:
        now        = datetime.utcnow()
        window_end = now + timedelta(minutes=35)

        sessions = (
            db.query(StudySchedule)
            .filter(
                StudySchedule.scheduled_time >  now,
                StudySchedule.scheduled_time <= window_end,
            )
            .all()
        )

        changed = False
        for s in sessions:
            mins = (s.scheduled_time - now).total_seconds() / 60

            if 28 <= mins <= 33 and not s.notified_30:
                _notify(db, s, 30)
                s.notified_30 = True
                changed = True
            elif 13 <= mins <= 17 and not s.notified_15:
                _notify(db, s, 15)
                s.notified_15 = True
                changed = True
            elif 3 <= mins <= 7 and not s.notified_5:
                _notify(db, s, 5)
                s.notified_5 = True
                changed = True

        if changed:
            db.commit()

    except Exception as exc:          # noqa: BLE001
        log.warning("Study notification job error: %s", exc)
        db.rollback()
    finally:
        db.close()


def _notify(db, schedule: StudySchedule, mins_before: int) -> None:
    local_time = schedule.scheduled_time.strftime("%I:%M %p")
    title   = f"⏰ Study Reminder — {schedule.course}"
    message = (
        f"Your {schedule.course} session starts in {mins_before} minutes "
        f"(at {local_time} UTC). Get ready!"
    )
    from ..services.push_service import notify_user
    notify_user(
        db, schedule.user_id, title, message,
        url="/study-strategy.html", commit=False,
    )


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.add_job(
            _check_study_notifications,
            "interval",
            minutes=1,
            id="study_notif",
            replace_existing=True,
        )
        _scheduler.start()
        log.info("Study notification scheduler started.")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Study notification scheduler stopped.")
