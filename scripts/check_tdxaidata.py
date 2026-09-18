from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.data import load_json
from dashboard.tdxaidata_provider import TdxAiDataSource


def main() -> int:
    config = load_json(ROOT / "config/scoring_candidate.json") or {}
    source = TdxAiDataSource(config.get("data_sources", {}).get("tdxaidata", {}))
    result = source.smoke()
    public = {key: value for key, value in result.items() if key != "comparisons"}
    print(json.dumps(public, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
