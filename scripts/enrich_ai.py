#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.ai_analysis import enrich_snapshot
from dashboard.builder import write_json
from dashboard.data import load_json


def main():
    parser = argparse.ArgumentParser(description="用可选AI模型增强盘面解读")
    parser.add_argument("--input", type=Path, default=ROOT / "site/latest.json")
    args = parser.parse_args()
    snapshot = load_json(args.input)
    if not snapshot:
        raise SystemExit(f"无法读取快照: {args.input}")
    result = enrich_snapshot(snapshot)
    write_json(args.input, result)
    status = result.get("ai_analysis", {}).get("status")
    print(json.dumps({"ai_analysis": status, "model": result.get("ai_analysis", {}).get("model")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
