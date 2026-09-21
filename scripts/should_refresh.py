from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.schedule import backup_run_is_timely, snapshot_is_fresh


def main() -> int:
    parser = argparse.ArgumentParser(description="Skip a backup run when a recent successful snapshot exists.")
    parser.add_argument("--snapshot", default="site/latest.json")
    parser.add_argument("--max-age-minutes", type=int, default=10)
    parser.add_argument("--max-lateness-minutes", type=int, default=8)
    parser.add_argument("--now", help="ISO timestamp, used by tests and diagnostics")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot = None
    fresh = snapshot_is_fresh(snapshot, now, args.max_age_minutes)
    timely = backup_run_is_timely(now, args.max_lateness_minutes)
    should_refresh = timely and not fresh
    if not timely:
        reason = "outside_backup_window"
    elif fresh:
        reason = "recent_success_exists"
    else:
        reason = "backup_required"
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"should_refresh={'true' if should_refresh else 'false'}\n")
    print(json.dumps({
        "should_refresh": should_refresh,
        "snapshot_fresh": fresh,
        "backup_window_timely": timely,
        "reason": reason,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
