#!/usr/bin/env python3
import json
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "site/latest.json"
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("status") != "SUCCESS":
    raise SystemExit("本次行情生成失败；失败状态已发布到页面。")
print("本次行情生成成功。")
