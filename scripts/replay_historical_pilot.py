#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.historical_replay import audit_artifact_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit immutable inputs before a V2.3 historical replay")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--trade-date", default="2026-09-03")
    parser.add_argument("--next-trade-date", default="2026-09-04")
    parser.add_argument("--minimum-coverage", type=float, default=0.85)
    args = parser.parse_args()
    result = audit_artifact_database(
        args.database,
        args.trade_date,
        args.next_trade_date,
        minimum_coverage=args.minimum_coverage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
