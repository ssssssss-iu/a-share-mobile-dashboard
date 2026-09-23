from __future__ import annotations

import gzip
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .data import mainboard


def _artifact_payload(path: str) -> object:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle).get("data")


def _minute_rows(payload: object) -> list:
    if not isinstance(payload, dict):
        return []
    blocks = payload.get("data") or {}
    if not isinstance(blocks, dict) or not blocks:
        return []
    block = next(iter(blocks.values()))
    return list((block or {}).get("m1") or [])


def assess_replay_readiness(
    universe_size: int,
    minute_ready: int,
    daily_ready: int,
    intersection_ready: int,
    next_session_ready: int,
    minimum_coverage: float = 0.85,
) -> dict:
    def ratio(value: int) -> float:
        return round(value / universe_size, 6) if universe_size else 0.0

    coverage = ratio(intersection_ready)
    blockers = []
    if coverage < minimum_coverage:
        blockers.append("POINT_IN_TIME_INPUT_COVERAGE_BELOW_GATE")
    if next_session_ready == 0:
        blockers.append("NEXT_SESSION_EXIT_PRICE_MISSING")
    return {
        "status": "READY" if not blockers else "DATA_INSUFFICIENT",
        "minimum_coverage": minimum_coverage,
        "universe_size": universe_size,
        "minute_ready": minute_ready,
        "minute_coverage": ratio(minute_ready),
        "daily_ready": daily_ready,
        "daily_coverage": ratio(daily_ready),
        "intersection_ready": intersection_ready,
        "intersection_coverage": coverage,
        "next_session_ready": next_session_ready,
        "next_session_coverage": ratio(next_session_ready),
        "blockers": blockers,
    }


def audit_artifact_database(
    database: Path,
    trade_date: str,
    next_trade_date: str,
    signal_time: str = "14:50",
    minimum_intraday_bars: int = 220,
    minimum_daily_bars: int = 61,
    minimum_coverage: float = 0.85,
) -> dict:
    compact_date = trade_date.replace("-", "")
    compact_next = next_trade_date.replace("-", "")
    cutoff = signal_time.replace(":", "")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        snapshot = connection.execute(
            """
            SELECT * FROM artifacts
            WHERE kind='market_snapshot' AND substr(fetched_at,1,10)=?
            ORDER BY fetched_at DESC LIMIT 1
            """,
            (trade_date,),
        ).fetchone()
        if snapshot is None:
            return {
                "status": "DATA_INSUFFICIENT",
                "scope": "historical_replay_input_audit",
                "trade_date": trade_date,
                "blockers": ["MARKET_UNIVERSE_SNAPSHOT_MISSING"],
            }
        market = _artifact_payload(snapshot["path"])
        quotes = (market or {}).get("quotes") or {}
        universe = {
            code
            for code, quote in quotes.items()
            if mainboard(str(code), str((quote or {}).get("name") or ""))
        }

        best_minutes: dict[str, int] = defaultdict(int)
        next_session = set()
        for artifact in connection.execute(
            "SELECT symbol,path FROM artifacts WHERE kind='minute' ORDER BY fetched_at"
        ):
            code = str(artifact["symbol"] or "")
            if code not in universe:
                continue
            rows = _minute_rows(_artifact_payload(artifact["path"]))
            visible = [
                row
                for row in rows
                if str(row[0]).startswith(compact_date)
                and ("0930" <= str(row[0])[8:] <= "1130" or "1300" <= str(row[0])[8:] <= cutoff)
            ]
            best_minutes[code] = max(best_minutes[code], len(visible))
            if any(
                str(row[0]).startswith(compact_next) and "0930" <= str(row[0])[8:] <= "1000"
                for row in rows
            ):
                next_session.add(code)

        minute_ready_codes = {
            code for code, count in best_minutes.items() if count >= minimum_intraday_bars
        }
        daily_ready_codes = {
            str(row["symbol"])
            for row in connection.execute(
                """
                SELECT symbol,MAX(rows_count) AS rows_count,MAX(last_time) AS last_time
                FROM artifacts WHERE kind='daily' GROUP BY symbol
                """
            )
            if str(row["symbol"] or "") in universe
            and int(row["rows_count"] or 0) >= minimum_daily_bars
            and str(row["last_time"] or "") >= trade_date
        }
        ready = minute_ready_codes & daily_ready_codes
        result = assess_replay_readiness(
            len(universe),
            len(minute_ready_codes),
            len(daily_ready_codes),
            len(ready),
            len(ready & next_session),
            minimum_coverage,
        )
        result.update(
            {
                "scope": "historical_replay_input_audit",
                "trade_date": trade_date,
                "next_trade_date": next_trade_date,
                "signal_time": signal_time,
                "minimum_intraday_bars": minimum_intraday_bars,
                "minimum_daily_bars": minimum_daily_bars,
                "market_snapshot_fetched_at": snapshot["fetched_at"],
                "performance": None,
                "interpretation": (
                    "只有输入覆盖率通过后才生成信号和收益；当前结果仅是回放数据审计。"
                ),
            }
        )
        return result
    finally:
        connection.close()
