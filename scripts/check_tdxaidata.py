from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.data import MarketClient, load_json
from dashboard.tdxaidata_provider import TdxAiDataSource


def full_validation(config: dict, source: TdxAiDataSource) -> dict:
    if not source.configured:
        result = source.smoke()
        result["validation_scope"] = "smoke_only_missing_token"
        return result
    snapshot = load_json(ROOT / "site/latest.json") or {}
    trade_date = snapshot.get("trade_date")
    rankings = snapshot.get("rankings") or []
    if not trade_date or not rankings:
        result = source.smoke()
        result["validation_scope"] = "smoke_only_no_snapshot"
        return result
    codes = [str(item.get("code") or "") for item in rankings[:5] if item.get("code")]
    client = MarketClient(
        minimum_quote_coverage=float(config.get("universe", {}).get("minimum_quote_coverage", 0.90))
    )
    details, detail_errors = client.details(codes, date.fromisoformat(trade_date))
    quotes = {str(item.get("code")): item for item in rankings if item.get("code")}
    result = source.validate(codes, trade_date, quotes, details, "CLOSED")
    result["validation_scope"] = "latest_top5_weekend_replay"
    result["reference_trade_date"] = trade_date
    result["reference_detail_successes"] = len(details)
    result["reference_detail_failures"] = len(detail_errors)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Compare the latest Top 5 quote, daily and minute data")
    args = parser.parse_args()
    config = load_json(ROOT / "config/scoring_candidate.json") or {}
    source = TdxAiDataSource(config.get("data_sources", {}).get("tdxaidata", {}))
    result = full_validation(config, source) if args.full else source.smoke()
    public = dict(result)
    public["comparison_codes"] = [item.get("code") for item in public.pop("comparisons", [])]
    print(json.dumps(public, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
