from __future__ import annotations

from datetime import datetime, timezone


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


def _minute(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _scheduled_datetime(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(now.tzinfo)
    if stamp > 10_000_000_000:
        stamp /= 1000
    return datetime.fromtimestamp(stamp, timezone.utc).astimezone(now.tzinfo)


def intraday_slot(now: datetime, trigger: str = "manual", scheduled_time: str | None = None) -> dict:
    """Resolve an update to a stable primary slot without disguising manual runs."""
    trigger = (trigger or "manual").lower()
    reference = _scheduled_datetime(scheduled_time, now) or now
    reference_minute = reference.hour * 60 + reference.minute

    if trigger == "cloudflare":
        candidates = PRIMARY_UPDATE_TIMES
        source = "primary"
        max_distance = 3
    elif trigger == "schedule":
        candidates = BACKUP_UPDATE_TIMES
        source = "backup"
        max_distance = 12
    else:
        stamp = now.strftime("%H:%M:%S")
        return {
            "key": f"manual-{now.strftime('%H%M%S')}",
            "label": f"手动 {stamp}",
            "planned_time": None,
            "kind": "manual",
            "source": trigger,
        }

    index, matched = min(
        enumerate(candidates),
        key=lambda item: abs(_minute(item[1]) - reference_minute),
    )
    if abs(_minute(matched) - reference_minute) > max_distance:
        stamp = now.strftime("%H:%M:%S")
        return {
            "key": f"late-{source}-{now.strftime('%H%M%S')}",
            "label": f"延迟 {stamp}",
            "planned_time": None,
            "kind": "manual",
            "source": source,
        }
    primary = PRIMARY_UPDATE_TIMES[index]
    return {
        "key": f"scheduled-{primary.replace(':', '')}",
        "label": primary,
        "planned_time": primary,
        "kind": "scheduled",
        "source": source,
    }


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
