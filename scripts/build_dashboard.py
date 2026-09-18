#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.builder import build


def main():
    parser = argparse.ArgumentParser(description="生成A股静态研究看板数据")
    parser.add_argument("--output", type=Path, default=ROOT / "site/latest.json")
    parser.add_argument("--history-dir", type=Path, default=ROOT / "site/history")
    parser.add_argument("--intraday-dir", type=Path, default=ROOT / "site/intraday")
    args = parser.parse_args()
    result = build(args.output, args.history_dir, intraday_dir=args.intraday_dir)
    print(json.dumps({"status": result["status"], "generated_at": result["generated_at"], "candidates": len(result["candidates"]), "error": result.get("error")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
