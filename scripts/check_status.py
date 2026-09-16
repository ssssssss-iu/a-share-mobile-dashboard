#!/usr/bin/env python3
import json
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "site/latest.json"
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("status") != "SUCCESS":
    raise SystemExit("本次行情生成失败；失败状态已发布到页面。")
diagnostics = payload.get("diagnostics") or {}
announcement_targets = int(diagnostics.get("announcement_targets") or 0)
announcement_failures = int(diagnostics.get("announcement_failures") or 0)
if announcement_targets and announcement_failures == announcement_targets:
    print(
        f"::warning::公告数据源全部失败（{announcement_failures}/{announcement_targets}）；"
        "E模块按0分发布候选。"
    )
print("本次行情生成成功。")

