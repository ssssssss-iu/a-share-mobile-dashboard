from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .data import load_json, number
from .schedule import intraday_slot


RESEARCH_SCHEMA_VERSION = "1.0"
LABEL_KEYS = (
    "forward_30m",
    "same_close",
    "next_open",
    "next_1000",
    "next_close",
    "day3_close",
    "day5_close",
    "mfe_5d",
    "mae_5d",
)


def _write_json(path: Path, payload: dict) -> None:
    from .builder import write_json

    write_json(path, payload)


def _return_pct(price, base):
    if number(price) is None or number(base) in (None, 0):
        return None
    return round((float(price) / float(base) - 1) * 100, 4)


def _label(price, base, observed_at, market_time, trade_date):
    return {
        "status": "OBSERVED",
        "price": price,
        "return_pct": _return_pct(price, base),
        "observed_at": observed_at,
        "market_time": market_time,
        "trade_date": trade_date,
    }


def _return_label(return_pct, observed_at, market_time, trade_date):
    return {
        "status": "OBSERVED",
        "return_pct": return_pct,
        "observed_at": observed_at,
        "market_time": market_time,
        "trade_date": trade_date,
    }


def _pending_labels():
    return {key: {"status": "PENDING"} for key in LABEL_KEYS}


def _update_signal_labels(signal: dict, quote: dict, now: datetime, current_trade_date: str, phase_code: str) -> bool:
    origin_date = signal.get("trade_date")
    entry_price = signal.get("entry_price")
    if not origin_date or number(entry_price) in (None, 0):
        return False
    labels = signal.setdefault("labels", _pending_labels())
    for key in LABEL_KEYS:
        labels.setdefault(key, {"status": "PENDING"})
    changed = False
    observed_at = now.isoformat(timespec="seconds")
    market_time = quote.get("market_time")

    if current_trade_date == origin_date:
        try:
            signal_time = datetime.fromisoformat(signal["generated_at"])
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=now.tzinfo)
            elapsed = (now - signal_time.astimezone(now.tzinfo)).total_seconds()
        except (KeyError, TypeError, ValueError):
            elapsed = -1
        if elapsed >= 30 * 60 and labels["forward_30m"].get("status") == "PENDING":
            labels["forward_30m"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
            changed = True
        if phase_code == "CLOSED" and labels["same_close"].get("status") == "PENDING":
            labels["same_close"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
            changed = True
        return changed

    if current_trade_date < origin_date:
        return changed

    future_dates = signal.setdefault("observed_future_trade_dates", [])
    if current_trade_date not in future_dates:
        future_dates.append(current_trade_date)
        future_dates.sort()
        changed = True
    day_number = future_dates.index(current_trade_date) + 1
    minute = now.hour * 60 + now.minute

    if day_number == 1 and minute >= 9 * 60 + 30 and labels["next_open"].get("status") == "PENDING":
        labels["next_open"] = _label(quote.get("open"), entry_price, observed_at, market_time, current_trade_date)
        changed = True
    if day_number == 1 and minute >= 10 * 60 and labels["next_1000"].get("status") == "PENDING":
        labels["next_1000"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
        changed = True
    if day_number == 1 and phase_code == "CLOSED" and labels["next_close"].get("status") == "PENDING":
        labels["next_close"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
        changed = True
    if day_number == 3 and phase_code == "CLOSED" and labels["day3_close"].get("status") == "PENDING":
        labels["day3_close"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
        changed = True
    if day_number == 5 and phase_code == "CLOSED" and labels["day5_close"].get("status") == "PENDING":
        labels["day5_close"] = _label(quote.get("price"), entry_price, observed_at, market_time, current_trade_date)
        changed = True

    if day_number <= 5:
        mfe = _return_pct(quote.get("high"), entry_price)
        mae = _return_pct(quote.get("low"), entry_price)
        excursions = signal.setdefault("excursions_5d", {"mfe_pct": None, "mae_pct": None, "last_trade_date": None})
        prior_mfe, prior_mae = excursions.get("mfe_pct"), excursions.get("mae_pct")
        if mfe is not None and (prior_mfe is None or mfe > prior_mfe):
            excursions["mfe_pct"] = mfe
            changed = True
        if mae is not None and (prior_mae is None or mae < prior_mae):
            excursions["mae_pct"] = mae
            changed = True
        if excursions.get("last_trade_date") != current_trade_date:
            excursions["last_trade_date"] = current_trade_date
            changed = True
        if day_number == 5 and phase_code == "CLOSED":
            if labels["mfe_5d"].get("status") == "PENDING":
                labels["mfe_5d"] = _return_label(
                    excursions.get("mfe_pct"), observed_at, market_time, current_trade_date
                )
                changed = True
            if labels["mae_5d"].get("status") == "PENDING":
                labels["mae_5d"] = _return_label(
                    excursions.get("mae_pct"), observed_at, market_time, current_trade_date
                )
                changed = True
    return changed


def update_research_outcomes(research_dir: Path, rows: list[dict], now: datetime, trade_date: str, phase_code: str) -> int:
    quote_by_code = {row.get("code"): row for row in rows if row.get("code")}
    updated_files = 0
    for path in sorted(research_dir.glob("????-??-??/*.json")):
        payload = load_json(path)
        if not payload:
            continue
        changed = False
        for signal in payload.get("signals") or []:
            quote = quote_by_code.get(signal.get("code"))
            if quote and _update_signal_labels(signal, quote, now, trade_date, phase_code):
                changed = True
        if changed:
            payload["labels_updated_at"] = now.isoformat(timespec="seconds")
            _write_json(path, payload)
            updated_files += 1
    return updated_files


def _compact_signal(item: dict, trade_date: str, generated_at: str, phase_code: str) -> dict:
    signal = deepcopy(item)
    signal["signal_id"] = f"{generated_at}:{item.get('code')}"
    signal["trade_date"] = trade_date
    signal["generated_at"] = generated_at
    signal["entry_price"] = item.get("price")
    signal["labels"] = _pending_labels()
    if phase_code == "CLOSED":
        signal["labels"]["same_close"] = _label(
            item.get("price"), item.get("price"), generated_at, item.get("market_time"), trade_date
        )
        signal["labels"]["forward_30m"] = {
            "status": "NOT_APPLICABLE",
            "reason": "信号生成时已收盘，无法观察同日30分钟后价格",
        }
    signal["observed_future_trade_dates"] = []
    signal["excursions_5d"] = {"mfe_pct": None, "mae_pct": None, "last_trade_date": None}
    return signal


def _update_index(research_dir: Path, keep_days: int) -> None:
    day_dirs = sorted((path for path in research_dir.glob("????-??-??") if path.is_dir()), reverse=True)
    for old_dir in day_dirs[keep_days:]:
        for path in old_dir.glob("*.json"):
            path.unlink()
        old_dir.rmdir()
    dates = []
    for day_dir in day_dirs[:keep_days]:
        files = sorted(day_dir.glob("*.json"))
        signal_count = 0
        observed_labels = 0
        for path in files:
            payload = load_json(path) or {}
            signals = payload.get("signals") or []
            signal_count += len(signals)
            observed_labels += sum(
                label.get("status") == "OBSERVED"
                for signal in signals
                for label in (signal.get("labels") or {}).values()
            )
        dates.append({
            "date": day_dir.name,
            "snapshot_count": len(files),
            "signal_count": signal_count,
            "observed_label_count": observed_labels,
        })
    _write_json(research_dir / "index.json", {"schema_version": RESEARCH_SCHEMA_VERSION, "dates": dates})


def record_research_snapshot(
    snapshot: dict,
    full_scores: list[dict],
    rows: list[dict],
    research_dir: Path,
    now: datetime,
    trigger: str = "manual",
    scheduled_time: str | None = None,
    keep_days: int = 30,
) -> dict:
    """Persist all fully scored stocks and backfill observable forward-return labels."""
    if snapshot.get("status") != "SUCCESS" or snapshot.get("data_context", {}).get("code") != "CURRENT_SESSION":
        return {"saved": False, "updated_files": 0}
    trade_date = snapshot.get("trade_date")
    if not trade_date:
        return {"saved": False, "updated_files": 0}

    research_dir.mkdir(parents=True, exist_ok=True)
    updated_files = update_research_outcomes(
        research_dir, rows, now, trade_date, (snapshot.get("phase") or {}).get("code", "")
    )
    slot = intraday_slot(now, trigger, scheduled_time)
    day_dir = research_dir / trade_date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{slot['key']}.json"
    saved = False
    if not path.exists():
        generated_at = snapshot.get("generated_at") or now.isoformat(timespec="seconds")
        payload = {
            "schema_version": RESEARCH_SCHEMA_VERSION,
            "trade_date": trade_date,
            "slot": slot,
            "generated_at": generated_at,
            "market": deepcopy(snapshot.get("market") or {}),
            "strategy": deepcopy(snapshot.get("strategy") or {}),
            "data_source_validation": deepcopy((snapshot.get("diagnostics") or {}).get("tdxaidata") or {}),
            "score_pool_codes": [item.get("code") for item in full_scores if item.get("in_score_pool")],
            "qualified_codes": {
                "ordinary": [item.get("code") for item in full_scores if item.get("channels", {}).get("ordinary", {}).get("qualified")],
                "hot": [item.get("code") for item in full_scores if item.get("channels", {}).get("hot", {}).get("qualified")],
            },
            "actionable_codes": {
                "ordinary": [item.get("code") for item in full_scores if item.get("channels", {}).get("ordinary", {}).get("actionable_now")],
                "hot": [item.get("code") for item in full_scores if item.get("channels", {}).get("hot", {}).get("actionable_now")],
            },
            "signals": [
                _compact_signal(item, trade_date, generated_at, (snapshot.get("phase") or {}).get("code", ""))
                for item in full_scores
            ],
        }
        _write_json(path, payload)
        saved = True
    _update_index(research_dir, keep_days)
    return {"saved": saved, "updated_files": updated_files, "path": str(path.relative_to(research_dir.parent))}
