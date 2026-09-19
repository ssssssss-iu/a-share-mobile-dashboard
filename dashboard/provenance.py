from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")


def parse_timestamp(value, trade_date: date | str | None = None) -> datetime | None:
    """Normalize common provider timestamps to timezone-aware Beijing time."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            parsed = datetime.fromtimestamp(number, TZ)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit() and len(text) in (8, 12, 14):
            try:
                if len(text) == 8:
                    parsed = datetime.strptime(text, "%Y%m%d")
                elif len(text) == 12:
                    parsed = datetime.strptime(text, "%Y%m%d%H%M")
                else:
                    parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
            except ValueError:
                parsed = None
        else:
            if len(text) <= 8 and ":" in text and trade_date:
                text = f"{str(trade_date)[:10]}T{text}"
            text = text.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def iso_timestamp(value, trade_date: date | str | None = None, timespec: str = "seconds") -> str | None:
    parsed = parse_timestamp(value, trade_date=trade_date)
    return parsed.isoformat(timespec=timespec) if parsed else None


def source_latency_seconds(provider_time, received_at) -> float | None:
    provider = parse_timestamp(provider_time)
    received = parse_timestamp(received_at)
    if not provider or not received:
        return None
    return round(max(0.0, (received - provider).total_seconds()), 3)


def latest_row_timestamp(rows: list[dict] | None, trade_date: date | str | None = None) -> str | None:
    parsed = []
    for row in rows or []:
        stamp = parse_timestamp(row.get("time") or row.get("date"), trade_date=trade_date)
        if stamp:
            parsed.append(stamp)
    return max(parsed).isoformat(timespec="seconds") if parsed else None
