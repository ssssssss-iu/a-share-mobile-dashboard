from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path


def _configure_token() -> Path:
    token = os.getenv("TDX_AI_DATA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TDX_AI_DATA_TOKEN is not configured")
    if "\n" in token or "\r" in token:
        raise RuntimeError("TDX_AI_DATA_TOKEN contains an invalid newline")
    spec = importlib.util.find_spec("tdxaidata")
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError("tdxaidata package is not installed")
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    ini_path = package_dir / "lib" / "TdxAiData.ini"
    content = ini_path.read_text(encoding="utf-8")
    updated, count = re.subn(r"(?m)^token=.*$", lambda _: f"token={token}", content, count=1)
    if count != 1:
        raise RuntimeError("TdxAiData.ini is missing the token setting")
    ini_path.write_text(updated, encoding="utf-8")
    return ini_path


def _clean_number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _records_from_market_data(data: dict, symbols: list[str]) -> dict[str, list[dict]]:
    output = {symbol: [] for symbol in symbols}
    if not isinstance(data, dict) or not data:
        return output
    frames = {key: value for key, value in data.items() if hasattr(value, "index") and hasattr(value, "columns")}
    if not frames:
        return output
    first = next(iter(frames.values()))
    for symbol in symbols:
        if symbol not in first.columns:
            continue
        for stamp in first.index:
            row = {"time": stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)}
            for field, frame in frames.items():
                if symbol not in frame.columns or stamp not in frame.index:
                    continue
                row[field.lower()] = _clean_number(frame.at[stamp, symbol])
            if row.get("close") is not None:
                output[symbol].append(row)
    return output


def _run(request: dict) -> dict:
    _configure_token()
    with redirect_stdout(sys.stderr):
        from tdxaidata import __version__, tqs

        symbols = [str(item).strip().upper() for item in request.get("symbols") or [] if str(item).strip()]
        if not symbols:
            raise RuntimeError("no symbols supplied")
        snapshots = {}
        snapshot_fields = ["Now", "LastClose", "Open", "Max", "Min", "Volume", "Amount", "Average"]
        batch_snapshot = hasattr(tqs, "get_market_snapshot_batch")
        if batch_snapshot:
            chunk_size = max(1, int(request.get("snapshot_chunk_size") or 50))
            for offset in range(0, len(symbols), chunk_size):
                chunk = symbols[offset:offset + chunk_size]
                value = tqs.get_market_snapshot_batch(chunk, snapshot_fields, return_df=False)
                if isinstance(value, dict):
                    snapshots.update(value)
        else:
            for symbol in symbols:
                value = tqs.get_market_snapshot(symbol, snapshot_fields)
                if isinstance(value, dict) and value:
                    snapshots[symbol] = value

        action = request.get("action", "validate")
        daily, minutes, auction = {}, {}, {}
        if action == "validate":
            detail_symbols = symbols[: max(1, int(request.get("detail_limit") or 5))]
            daily_raw = tqs.get_market_data(
                field_list=["Open", "High", "Low", "Close", "Volume", "Amount"],
                stock_list=detail_symbols,
                period="1d",
                end_time=str(request.get("trade_date") or ""),
                count=max(21, int(request.get("daily_count") or 90)),
                dividend_type="front",
            )
            minute_raw = tqs.get_market_data(
                field_list=["Open", "High", "Low", "Close", "Volume", "Amount"],
                stock_list=detail_symbols,
                period="1m",
                end_time=str(request.get("trade_date") or ""),
                count=max(30, int(request.get("minute_count") or 320)),
                dividend_type="none",
            )
            daily = _records_from_market_data(daily_raw, detail_symbols)
            minutes = _records_from_market_data(minute_raw, detail_symbols)
            auction_supported = hasattr(tqs, "get_call_auction_batch")
            if request.get("include_auction") and auction_supported:
                value = tqs.get_call_auction_batch(
                    detail_symbols,
                    ["Time", "Price", "Volume", "LeaveQty", "InOutFlag", "TotalNum"],
                    return_df=False,
                )
                if isinstance(value, dict):
                    auction = value
    return {
        "status": "CONNECTED" if snapshots else "EMPTY",
        "package_version": __version__,
        "snapshots": snapshots,
        "daily": daily,
        "minutes": minutes,
        "auction": auction,
        "capabilities": {
            "batch_snapshot": batch_snapshot,
            "call_auction": hasattr(tqs, "get_call_auction_batch"),
        },
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        result = _run(request)
        json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        token = os.getenv("TDX_AI_DATA_TOKEN", "")
        message = str(exc)[:300]
        if token:
            message = message.replace(token, "***")
        print(f"TdxAiData bridge failed: {type(exc).__name__}: {message}", file=sys.stderr)
        json.dump({"status": "FAILED", "error_type": type(exc).__name__, "error_message": message}, sys.stdout)
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
