from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .data import load_json
from .schedule import intraday_slot


HISTORY_SCHEMA_VERSION = "1.1"


def _market_source(snapshot: dict) -> dict:
    for source in snapshot.get("sources") or []:
        if "coverage" in source:
            return {
                "name": source.get("source"),
                "coverage": source.get("coverage"),
                "minimum_coverage": source.get("minimum_coverage"),
                "failed_pages": source.get("failed_pages"),
            }
    return {}


def _module_map(item: dict) -> dict:
    return {
        module.get("key"): {
            "label": module.get("label"),
            "score": module.get("score"),
            "max": module.get("max"),
            "state": module.get("state"),
        }
        for module in item.get("modules") or []
        if module.get("key")
    }


def _change_summary(change_type: str, rank_delta: int | None, score_delta: int | float | None, module_deltas: list[dict]) -> str:
    if change_type == "FIRST_ENTRY":
        return "当日首次进入前五"
    if change_type == "REENTRY":
        return "退出后再次进入前五"
    parts = []
    if rank_delta:
        parts.append(f"排名{'上升' if rank_delta > 0 else '下降'}{abs(rank_delta)}位")
    else:
        parts.append("排名不变")
    if score_delta:
        parts.append(f"总分{'增加' if score_delta > 0 else '减少'}{abs(score_delta):g}分")
    leading = sorted(module_deltas, key=lambda item: abs(item["delta"]), reverse=True)
    if leading:
        module = leading[0]
        parts.append(f"主要来自{module['key']} {module['label']} {module['delta']:+g}分")
    return "，".join(parts)


def _compact_ranking(item: dict, previous: dict | None, first_seen: dict | None, slot: dict, generated_at: str) -> dict:
    modules = _module_map(item)
    previous_modules = (previous or {}).get("modules") or {}
    module_deltas = []
    for key, module in modules.items():
        if key not in previous_modules:
            continue
        delta = (module.get("score") or 0) - (previous_modules[key].get("score") or 0)
        if delta:
            module_deltas.append({"key": key, "label": module.get("label") or key, "delta": delta})

    if previous:
        rank_delta = (previous.get("rank") or 0) - (item.get("rank") or 0)
        score_delta = (item.get("score") or 0) - (previous.get("score") or 0)
        change_type = "UP" if rank_delta > 0 else "DOWN" if rank_delta < 0 else "SAME"
    else:
        rank_delta = None
        score_delta = None
        change_type = "REENTRY" if first_seen else "FIRST_ENTRY"

    first_seen = first_seen or {
        "slot": slot["label"],
        "generated_at": generated_at,
        "price": item.get("price"),
    }
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "sector": item.get("sector"),
        "rank": item.get("rank"),
        "score": item.get("score"),
        "price": item.get("price"),
        "change_pct": item.get("change_pct"),
        "market_time": item.get("market_time"),
        "in_candidate_pool": bool(item.get("in_candidate_pool")),
        "in_score_pool": bool(item.get("in_score_pool", item.get("in_candidate_pool"))),
        "data_confidence": deepcopy(item.get("data_confidence") or {}),
        "modules": modules,
        "plan": deepcopy(item.get("plan") or {}),
        "channels": deepcopy(item.get("channels") or {}),
        "first_seen": first_seen,
        "change": {
            "type": change_type,
            "rank_delta": rank_delta,
            "score_delta": score_delta,
            "module_deltas": module_deltas,
            "summary": _change_summary(change_type, rank_delta, score_delta, module_deltas),
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    from .builder import write_json

    write_json(path, payload)


def _update_index(history_dir: Path, keep_days: int) -> None:
    day_paths = sorted(
        (path for path in history_dir.glob("????-??-??.json") if path.name != "index.json"),
        reverse=True,
    )
    for old_path in day_paths[keep_days:]:
        old_path.unlink()
    dates = []
    for path in day_paths[:keep_days]:
        payload = load_json(path) or {}
        snapshots = payload.get("snapshots") or []
        dates.append({
            "date": payload.get("trade_date") or path.stem,
            "snapshot_count": len(snapshots),
            "last_generated_at": snapshots[-1].get("generated_at") if snapshots else None,
        })
    _write_json(history_dir / "index.json", {"schema_version": HISTORY_SCHEMA_VERSION, "dates": dates})


def record_intraday_snapshot(
    snapshot: dict,
    history_dir: Path,
    now: datetime,
    trigger: str = "manual",
    scheduled_time: str | None = None,
    keep_days: int = 30,
) -> bool:
    """Append one immutable successful Top-5 snapshot and return whether it was saved."""
    if snapshot.get("status") != "SUCCESS" or snapshot.get("data_context", {}).get("code") != "CURRENT_SESSION":
        return False
    trade_date = snapshot.get("trade_date")
    rankings = snapshot.get("rankings") or []
    if not trade_date or not rankings:
        return False

    slot = intraday_slot(now, trigger, scheduled_time)
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{trade_date}.json"
    day = load_json(path) or {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "trade_date": trade_date,
        "snapshots": [],
    }
    snapshots = day.get("snapshots") or []
    if any(item.get("slot", {}).get("key") == slot["key"] for item in snapshots):
        return False

    previous_snapshot = snapshots[-1] if snapshots else None
    previous_by_code = {item["code"]: item for item in (previous_snapshot or {}).get("rankings", [])}
    first_seen_by_code = {}
    for recorded in snapshots:
        for item in recorded.get("rankings") or []:
            first_seen_by_code.setdefault(item["code"], item.get("first_seen"))

    generated_at = snapshot.get("generated_at") or now.isoformat(timespec="seconds")
    compact = [
        _compact_ranking(
            item,
            previous_by_code.get(item.get("code")),
            first_seen_by_code.get(item.get("code")),
            slot,
            generated_at,
        )
        for item in rankings[:5]
    ]
    current_codes = {item["code"] for item in compact}
    exited = [
        {"code": item.get("code"), "name": item.get("name"), "previous_rank": item.get("rank")}
        for item in (previous_snapshot or {}).get("rankings", [])
        if item.get("code") not in current_codes
    ]
    day["snapshots"] = snapshots + [{
        "slot": slot,
        "generated_at": generated_at,
        "market_time": max((item.get("market_time") or "" for item in compact), default=None),
        "strategy": {
            "id": snapshot.get("strategy", {}).get("id"),
            "version": snapshot.get("strategy", {}).get("version"),
            "automation_profile": snapshot.get("strategy", {}).get("automation_profile"),
        },
        "data_quality": _market_source(snapshot),
        "rankings": compact,
        "exited": exited,
    }]
    _write_json(path, day)
    _update_index(history_dir, keep_days)
    return True
