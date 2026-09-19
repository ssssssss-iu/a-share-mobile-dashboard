from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime

from .data import number
from .provenance import TZ, iso_timestamp, latest_row_timestamp, source_latency_seconds


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

    def _request_payload(self, codes: list[str], trade_date: date | str, phase_code: str) -> dict:
        default_limit = self.config.get("primary_limit", 36) if self.mode == "primary" else self.config.get("shadow_limit", 5)
        limit = max(1, int(default_limit))
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
            return self._call(request)
        except subprocess.TimeoutExpired:
            return {"status": "FAILED", "error_type": "TimeoutExpired"}

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
            "message": (
                "数据服务连接成功，交易时段将作为评分主源"
                if status == "CONNECTED" and self.mode == "primary"
                else "数据服务连接成功，等待交易日影子对照"
                if status == "CONNECTED"
                else "连接失败，继续使用原数据源"
            ),
            "error_type": payload.get("error_type"),
            "error_message": payload.get("error_message"),
        }

    def validate(self, codes: list[str], trade_date: date | str, quotes: dict, details: dict, phase_code: str) -> dict:
        base = self._base_status()
        if base:
            return base
        payload = self._request_payload(codes, trade_date, phase_code)
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

    def primary_data(
        self,
        codes: list[str],
        trade_date: date | str,
        phase_code: str,
        fallback_quotes: dict[str, dict],
        fallback_details: dict[str, dict],
        observed_at: datetime,
    ) -> tuple[dict[str, dict], dict[str, dict], dict]:
        """Fetch the scoring quote/K-line set from TdxAiData, with per-code fallback."""
        base = self._base_status()
        if base:
            marked_quotes = {
                code: {
                    **(quote or {}),
                    "source_fallback": True,
                    "source_fallback_reason": base.get("message") or "TdxAiData未提供主源数据",
                }
                for code, quote in fallback_quotes.items()
            }
            return marked_quotes, fallback_details, {
                **base,
                "affects_scoring": False,
                "primary_quote_count": 0,
                "primary_detail_count": 0,
                "fallback_quote_count": len(codes),
                "fallback_detail_count": len(codes),
                "requested_count": len(codes),
                "received_at": observed_at.isoformat(timespec="seconds"),
            }
        payload = self._request_payload(codes, trade_date, phase_code)
        received_at = datetime.now(observed_at.tzinfo or TZ)
        if payload.get("status") not in ("CONNECTED", "EMPTY"):
            marked_quotes = {
                code: {
                    **(quote or {}),
                    "source_fallback": True,
                    "source_fallback_reason": "TdxAiData主源请求失败",
                }
                for code, quote in fallback_quotes.items()
            }
            return marked_quotes, fallback_details, {
                "enabled": True,
                "mode": self.mode,
                "status": "PRIMARY_WITH_FALLBACK",
                "package_version": payload.get("package_version"),
                "message": "TdxAiData主源请求失败，逐只回退原行情",
                "error_type": payload.get("error_type"),
                "error_message": payload.get("error_message"),
                "affects_scoring": False,
                "primary_quote_count": 0,
                "primary_detail_count": 0,
                "fallback_quote_count": len(codes),
                "fallback_detail_count": len(codes),
                "requested_count": len(codes),
                "received_at": received_at.isoformat(timespec="seconds"),
            }

        snapshots = payload.get("snapshots") or {}
        daily = payload.get("daily") or {}
        minutes = payload.get("minutes") or {}
        quotes = {}
        details = {}
        primary_quotes = 0
        primary_details = 0
        provenance = []
        for code in codes:
            code = plain_code(code)
            fallback_quote = dict(fallback_quotes.get(code) or {})
            raw = snapshots.get(tdx_symbol(code)) or {}
            now = number(raw.get("Now"))
            previous_close = number(raw.get("LastClose"))
            snapshot_time = None
            for key in ("Time", "time", "MarketTime", "market_time", "UpdateTime", "update_time", "DateTime", "datetime", "Timestamp", "timestamp"):
                if raw.get(key) not in (None, ""):
                    snapshot_time = iso_timestamp(raw.get(key), trade_date=trade_date)
                    if snapshot_time:
                        break
            if now is not None:
                primary_quotes += 1
                quote = dict(fallback_quote)
                quote_timestamp = snapshot_time or quote.get("market_time")
                quote.update({
                    "price": now,
                    "previous_close": previous_close if previous_close is not None else quote.get("previous_close"),
                    "open": number(raw.get("Open")) or quote.get("open"),
                    "high": number(raw.get("Max")) or quote.get("high"),
                    "low": number(raw.get("Min")) or quote.get("low"),
                    "amount": number(raw.get("Amount")) or quote.get("amount"),
                    "market_time": quote_timestamp,
                    "data_source": "TdxAiData",
                    "source_fallback": False,
                    "received_at": received_at.isoformat(timespec="seconds"),
                    "source_latency_seconds": source_latency_seconds(snapshot_time, received_at),
                    "timestamp_source": "tdx_snapshot" if snapshot_time else "fallback_quote",
                })
                if quote.get("previous_close"):
                    quote["change_pct"] = round((now / quote["previous_close"] - 1) * 100, 4)
                quotes[code] = quote
            else:
                fallback_quote["source_fallback"] = True
                fallback_quote["source_fallback_reason"] = "TdxAiData缺少实时快照"
                quotes[code] = fallback_quote

            symbol = tdx_symbol(code)
            daily_rows = [
                {"date": str(row.get("time") or "")[:10], **{k: row.get(k) for k in ("open", "close", "high", "low", "volume")}}
                for row in daily.get(symbol) or []
                if str(row.get("time") or "")[:10]
            ]
            minute_rows = [
                {k: row.get(k) for k in ("time", "open", "close", "high", "low", "volume")}
                for row in minutes.get(symbol) or []
                if row.get("time")
            ]
            detail_provider_time = latest_row_timestamp(minute_rows, trade_date=trade_date)
            daily_provider_time = latest_row_timestamp(daily_rows, trade_date=trade_date)
            if now is not None and not snapshot_time and detail_provider_time and code in quotes:
                quotes[code]["market_time"] = detail_provider_time
                quotes[code]["source_latency_seconds"] = source_latency_seconds(detail_provider_time, received_at)
                quotes[code]["timestamp_source"] = "tdx_minute"
            if len(daily_rows) >= 21 and minute_rows:
                primary_details += 1
                details[code] = {
                    "daily": daily_rows,
                    "minute": minute_rows,
                    "provenance": {
                        "quote_source": "TdxAiData实时行情" if now is not None else "原行情回退",
                        "daily_source": "TdxAiData日K",
                        "minute_source": "TdxAiData分钟K",
                        "received_at": received_at.isoformat(timespec="seconds"),
                        "daily_provider_time": daily_provider_time,
                        "minute_provider_time": detail_provider_time,
                        "daily_latency_seconds": None,
                        "minute_latency_seconds": source_latency_seconds(detail_provider_time, received_at),
                        "timestamp_source": "tdx_records" if detail_provider_time or daily_provider_time else "missing",
                        "tdx_fallback": False,
                        "fallback": False,
                    },
                }
            else:
                details[code] = dict(fallback_details.get(code) or {})
                fallback_provenance = dict(details[code].get("provenance") or {})
                fallback_provenance.update({
                    "quote_source": "原行情回退",
                    "daily_source": "原日K回退",
                    "minute_source": "原分钟K回退",
                    "received_at": fallback_provenance.get("received_at") or received_at.isoformat(timespec="seconds"),
                    "tdx_fallback": True,
                    "fallback": True,
                })
                details[code]["provenance"] = fallback_provenance
            provenance.append({
                "code": code,
                "quote_source": "TdxAiData" if now is not None else "fallback",
                "daily_source": "TdxAiData" if len(daily_rows) >= 21 else "fallback",
                "minute_source": "TdxAiData" if minute_rows else "fallback",
                "provider_time": snapshot_time or detail_provider_time or daily_provider_time,
                "received_at": received_at.isoformat(timespec="seconds"),
                "source_latency_seconds": source_latency_seconds(snapshot_time or detail_provider_time or daily_provider_time, received_at),
                "timestamp_source": "tdx" if snapshot_time or detail_provider_time or daily_provider_time else "fallback_or_missing",
                "fallback": now is None or len(daily_rows) < 21 or not minute_rows,
            })
        status = "CONNECTED" if primary_quotes == len(codes) and primary_details == len(codes) else "PRIMARY_WITH_FALLBACK"
        provider_times = [item["provider_time"] for item in provenance if item.get("provider_time")]
        return quotes, details, {
            "enabled": True,
            "mode": self.mode,
            "status": status,
            "package_version": payload.get("package_version"),
            "message": "TdxAiData已作为评分主源" if status == "CONNECTED" else "TdxAiData部分缺失，已逐只回退原行情",
            "error_type": None,
            "error_message": None,
            "affects_scoring": primary_quotes > 0 or primary_details > 0,
            "primary_quote_count": primary_quotes,
            "primary_detail_count": primary_details,
            "fallback_quote_count": len(codes) - primary_quotes,
            "fallback_detail_count": len(codes) - primary_details,
            "requested_count": len(codes),
            "received_at": received_at.isoformat(timespec="seconds"),
            "provider_time_min": min(provider_times) if provider_times else None,
            "provider_time_max": max(provider_times) if provider_times else None,
            "timestamp_source_counts": {
                "tdx": sum(item["timestamp_source"] == "tdx" for item in provenance),
                "tdx_records": sum(item["timestamp_source"] == "tdx_records" for item in provenance),
                "fallback_or_missing": sum(item["timestamp_source"] == "fallback_or_missing" for item in provenance),
            },
            "provenance": provenance,
        }


def source_record(status: dict) -> dict:
    labels = {
        "CONNECTED": "连接成功",
        "EMPTY": "返回为空",
        "FAILED": "回退原源",
        "MISSING_TOKEN": "未配置Key",
        "DISABLED": "未启用",
        "PRIMARY_WITH_FALLBACK": "主源部分回退",
    }
    state = labels.get(status.get("status"), str(status.get("status") or "未知"))
    mode = "影子验证" if status.get("mode") == "shadow" else "主源"
    return {
        "source": f"通达信TdxAiData（{mode}·{state}）",
        "source_url": OFFICIAL_DOCS,
        "status": status.get("status"),
        "mode": status.get("mode"),
        "received_at": status.get("received_at"),
        "provider_time_min": status.get("provider_time_min"),
        "provider_time_max": status.get("provider_time_max"),
        "primary_quote_count": status.get("primary_quote_count"),
        "primary_detail_count": status.get("primary_detail_count"),
        "fallback_quote_count": status.get("fallback_quote_count"),
        "fallback_detail_count": status.get("fallback_detail_count"),
    }
