#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.data import load_json
from dashboard.strategy_validation import dumps_validation, validate_strategy


def main():
    cfg = load_json(ROOT / "config/scoring_candidate.json") or {}
    research = cfg.get("research") or {}
    parser = argparse.ArgumentParser(description="在终端计算策略前向验证摘要，不生成报告文件")
    parser.add_argument("--research-dir", type=Path, default=ROOT / "site/research")
    parser.add_argument("--minimum-samples", type=int, default=int(research.get("minimum_unique_stock_days", 100)))
    parser.add_argument("--round-trip-cost-bps", type=float, default=float(research.get("round_trip_cost_bps", 20)))
    args = parser.parse_args()
    print(
        dumps_validation(
            validate_strategy(
                args.research_dir,
                args.minimum_samples,
                args.round_trip_cost_bps,
                strategy_version=cfg.get("strategy_version"),
            )
        )
    )


if __name__ == "__main__":
    main()
