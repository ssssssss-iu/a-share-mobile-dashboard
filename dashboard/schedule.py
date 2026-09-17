from __future__ import annotations

from datetime import datetime


PRIMARY_UPDATE_TIMES = (
    "09:25", "09:40", "09:55",
    "10:10", "10:25", "10:40", "10:55",
    "11:10", "11:25",
    "13:05", "13:20", "13:35", "13:50",
    "14:05", "14:20", "14:35", "14:50",
    "15:05",
)

BACKUP_UPDATE_TIMES = (
    "09:30", "09:45",
    "10:00", "10:15", "10:30", "10:45",
    "11:00", "11:15", "11:30",
    "13:10", "13:25", "13:40", "13:55",
    "14:10", "14:25", "14:40", "14:55",
    "15:10",
)


def should_write_close_history(now: datetime) -> bool:
    """Keep one daily close snapshot; allow the 15:10 backup to replace it."""
    return now.hour == 15 and 5 <= now.minute < 20


def snapshot_is_fresh(snapshot: dict | None, now: datetime, max_age_minutes: int = 10) -> bool:
    if not snapshot or snapshot.get("status") != "SUCCESS" or not snapshot.get("generated_at"):
        return False
    try:
        generated_at = datetime.fromisoformat(snapshot["generated_at"])
    except (TypeError, ValueError):
        return False
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=now.tzinfo)
    age_seconds = (now - generated_at.astimezone(now.tzinfo)).total_seconds()
    return 0 <= age_seconds < max_age_minutes * 60
