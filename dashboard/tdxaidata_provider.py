from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date

from .data import number


OFFICIAL_DOCS = "https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114.html"


def tdx_symbol(code: str) -> str:
    code = str(code or "").strip().zfill(6)
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def plain_code(symbol: str) -> str:
    return str(symbol or "").split(".", 1)[0].zfill(6)


def _pct_diff(left, right):
    left_value, right_value = number(left), number(right)
    if left_value is None or right_value in (None, 0):
        return None
    return round(abs(left_value / right_value - 1) * 100, 4)


def _last_for_date(rows: list[dict], trade_date: str) -> dict | None:
    selected = [item for item in rows or [] if str(item.get("time") or "")[:10] == trade_date]
    return selected[-1] if selected else None


def summarize_validation(payload: dict, quotes: dict, details: dict, trade_date: str, tolerance_pct: float) -> dict:
    snapshots = payload.get("snapshots") or {}
    daily = payload.get("daily") or {}
    minutes = payload.get("minutes") or {}
    comparisons = []
    quote_matches = 0
    daily_matches = 0
    minute_matches = 0
    for symbol, raw in snapshots.items():
        code = plain_code(symbol)
        quote = quotes.get(code) or {}
        price_diff = _pct_diff(raw.get("Now"), quote.get("price")) if isinstance(raw, dict) else None
        if price_diff is not None and price_diff <= tolerance_pct:
            quote_matches += 1

        current_daily = (details.get(code) or {}).get("daily") or []
        current_day = next((item for item in reversed(current_daily) if item.get("date") == trade_date), None)
        tdx_day = _last_for_date(daily.get(symbol) or [], trade_date)
        daily_diff = _pct_diff((tdx_day or {}).get("close"), (current_day or {}).get("close"))
        if daily_diff is not None and daily_diff <= tolerance_pct:
            daily_matches += 1

        current_minutes = (details.get(code) or {}).get("minute") or []
        current_minute = _last_for_date(current_minutes, trade_date)
        tdx_minute = _last_for_date(minutes.get(symbol) or [], trade_date)
        minute_diff = _pct_diff((tdx_minute or {}).get("close"), (current_minute or {}).get("close"))
        if minute_diff is not None and minute_diff <= tolerance_pct:
            minute_matches += 1
        comparisons.append({
            "code": code,
            "quote_diff_pct": price_diff,
            "daily_close_diff_pct": daily_diff,
            "minute_close_diff_pct": minute_diff,
            "tdx_market_price": number(raw.get("Now")) if isinstance(raw, dict) else None,
            "current_market_price": number(quote.get("price")),
        })
    checked = len(comparisons)
    detail_checked = sum(item["daily_close_diff_pct"] is not None for item in comparisons)
    minute_checked = sum(item["minute_close_diff_pct"] is not None for item in comparisons)
    return {
        "status": "CONNECTED" if checked else "EMPTY",
        "package_version": payload.get("package_version"),
        "checked_codes": checked,
        "quote_match_count": quote_matches,
        "quote_match_ratio": round(quote_matches / checked, 4) if checked else None,
        "daily_match_count": daily_matches,
        "daily_checked_count": detail_checked,
        "minute_match_count": minute_matches,
        "minute_checked_count": minute_checked,
        "tolerance_pct": tolerance_pct,
        "auction_records": len(payload.get("auction") or {}),
        "comparisons": comparisons,
    }


class TdxAiDataSource:
    def __init__(self, config: dict | None = None, timeout: int = 180, environ: dict | None = None):
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", False))
        self.mode = str(self.config.get("mode", "shadow"))
        self.timeout = timeout
        self.environ = dict(os.environ if environ is None else environ)

    @property
    def configured(self) -> bool:
        return bool(str(self.environ.get("TDX_AI_DATA_TOKEN") or "").strip())

    def _call(self, request: dict, timeout: int | None = None) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "dashboard.tdxaidata_bridge"],
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout or self.timeout,
            env=self.environ,
        )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            payload = {"status": "FAILED", "error_type": "InvalidBridgeOutput"}
        if result.returncode != 0:
            payload["status"] = "FAILED"
            payload.setdefault("error_type", "BridgeProcessError")
        return payload

    def _base_status(self) -> dict | None:
        if not self.enabled:
            return {"enabled": False, "mode": self.mode, "status": "DISABLED", "message": "TdxAiData未启用"}
        if not self.configured:
            return {
                "enabled": True,
                "mode": self.mode,
                "status": "MISSING_TOKEN",
                "message": "未检测到GitHub Secret TDX_AI_DATA_TOKEN，继续使用原数据源",
            }
        return None

    def smoke(self) -> dict:
        base = self._base_status()
        if base:
            return base
        try:
            payload = self._call(
                {"action": "smoke", "symbols": ["000001.SZ"]},
                timeout=min(self.timeout, int(self.config.get("smoke_timeout_seconds", 60))),
            )
        except subprocess.TimeoutExpired:
            payload = {"status": "FAILED", "error_type": "TimeoutExpired"}
        status = payload.get("status", "FAILED")
        return {
            "enabled": True,
            "mode": self.mode,
            "status": status,
            "package_version": payload.get("package_version"),
            "checked_codes": len(payload.get("snapshots") or {}),
            "message": "数据服务连接成功，等待交易日影子对照" if status == "CONNECTED" else "连接失败，继续使用原数据源",
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
        }

    def validate(self, codes: list[str], trade_date: date | str, quotes: dict, details: dict, phase_code: str) -> dict:
        base = self._base_status()
        if base:
            return base
        limit = max(1, int(self.config.get("shadow_limit", 5)))
        selected = [tdx_symbol(code) for code in codes[:limit]]
        request = {
            "action": "validate",
            "symbols": selected,
            "trade_date": str(trade_date),
            "detail_limit": limit,
            "daily_count": int(self.config.get("daily_count", 90)),
            "minute_count": int(self.config.get("minute_count", 320)),
            "include_auction": phase_code == "AUCTION",
        }
        try:
            payload = self._call(request)
        except subprocess.TimeoutExpired:
            payload = {"status": "FAILED", "error_type": "TimeoutExpired"}
        if payload.get("status") not in ("CONNECTED", "EMPTY"):
            return {
                "enabled": True,
                "mode": self.mode,
                "status": "FAILED",
                "message": "TdxAiData验证失败，当前评分继续使用原数据源",
                "error_type": payload.get("error_type"),
                "error_message": payload.get("error_message"),
            }
        result = summarize_validation(
            payload,
            quotes,
            details,
            str(trade_date),
            float(self.config.get("maximum_price_diff_pct", 0.30)),
        )
        result.update({
            "enabled": True,
            "mode": self.mode,
            "shadow_days_required": int(self.config.get("shadow_days", 5)),
            "affects_scoring": self.mode == "primary",
            "message": "影子对照已完成，本次不改变评分" if self.mode == "shadow" else "TdxAiData主源模式",
        })
        return result


def source_record(status: dict) -> dict:
    labels = {
        "CONNECTED": "连接成功",
        "EMPTY": "返回为空",
        "FAILED": "回退原源",
        "MISSING_TOKEN": "未配置Key",
        "DISABLED": "未启用",
    }
    state = labels.get(status.get("status"), str(status.get("status") or "未知"))
    mode = "影子验证" if status.get("mode") == "shadow" else "主源"
    return {
        "source": f"通达信TdxAiData（{mode}·{state}）",
        "source_url": OFFICIAL_DOCS,
        "status": status.get("status"),
        "mode": status.get("mode"),
    }
