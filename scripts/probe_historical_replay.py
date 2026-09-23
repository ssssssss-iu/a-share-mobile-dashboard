#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if not os.getenv("TDX_AI_DATA_TOKEN", "").strip():
        print(json.dumps({"status": "MISSING_TOKEN"}, ensure_ascii=False))
        return 1
    request = {
        "action": "history_probe",
        "symbols": ["000001.SZ"],
        "trade_date": "2026-09-23",
        "detail_limit": 1,
        "daily_count": 120,
        "minute_period": "15m",
        "minute_count": 2000,
    }
    completed = subprocess.run(
        [sys.executable, str(ROOT / "dashboard/tdxaidata_bridge.py")],
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
        env=os.environ.copy(),
    )
    payload = json.loads(completed.stdout or "{}")
    symbol = request["symbols"][0]
    daily = (payload.get("daily") or {}).get(symbol) or []
    minutes = (payload.get("minutes") or {}).get(symbol) or []
    result = {
        "status": payload.get("status", "FAILED"),
        "package_version": payload.get("package_version"),
        "requested": {
            "symbol": symbol,
            "end_date": request["trade_date"],
            "daily_count": request["daily_count"],
            "minute_period": request["minute_period"],
            "minute_count": request["minute_count"],
        },
        "received": {
            "daily_count": len(daily),
            "daily_start": daily[0].get("time") if daily else None,
            "daily_end": daily[-1].get("time") if daily else None,
            "minute_count": len(minutes),
            "minute_start": minutes[0].get("time") if minutes else None,
            "minute_end": minutes[-1].get("time") if minutes else None,
        },
        "three_month_intraday_ready": len(minutes) >= 1000,
        "error_type": payload.get("error_type"),
        "error_message": payload.get("error_message"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["three_month_intraday_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
