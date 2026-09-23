from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median

from .data import load_json, number


def _score_band(score) -> str:
    value = float(score or 0)
    if value >= 80:
        return "80-100"
    if value >= 70:
        return "70-79"
    if value >= 60:
        return "60-69"
    return "0-59"


def _observed_return(signal: dict, label_key: str):
    label = (signal.get("labels") or {}).get(label_key) or {}
    value = label.get("return_pct")
    return float(value) if label.get("status") == "OBSERVED" and number(value) is not None else None


def _metrics(signals: list[dict], label_key: str, round_trip_cost_bps: float) -> dict:
    values = [value for signal in signals if (value := _observed_return(signal, label_key)) is not None]
    cost_pct = round_trip_cost_bps / 100
    net_values = [value - cost_pct for value in values]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value < 0]
    average_win = mean(wins) if wins else None
    average_loss_abs = abs(mean(losses)) if losses else None
    payoff_ratio = average_win / average_loss_abs if average_win is not None and average_loss_abs else None
    profit_factor = sum(wins) / abs(sum(losses)) if wins and losses and sum(losses) else None
    return {
        "label": label_key,
        "observations": len(values),
        "gross_mean_pct": round(mean(values), 4) if values else None,
        "gross_median_pct": round(median(values), 4) if values else None,
        "win_rate": round(len(wins) / len(net_values), 4) if net_values else None,
        "average_win_after_cost_pct": round(average_win, 4) if average_win is not None else None,
        "average_loss_after_cost_pct": round(average_loss_abs, 4) if average_loss_abs is not None else None,
        "payoff_ratio": round(payoff_ratio, 4) if payoff_ratio is not None else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "net_mean_after_cost_pct": round(mean(net_values), 4) if net_values else None,
        "round_trip_cost_bps": round_trip_cost_bps,
    }


def _load_signals(research_dir: Path, strategy_version: str | None = None) -> list[dict]:
    signals = []
    for path in sorted(research_dir.glob("????-??-??/*.json")):
        payload = load_json(path) or {}
        payload_version = ((payload.get("strategy") or {}).get("version"))
        if strategy_version and payload_version != strategy_version:
            continue
        trade_date = payload.get("trade_date")
        for original in payload.get("signals") or []:
            signal = dict(original)
            signal.setdefault("trade_date", trade_date)
            signal.setdefault("strategy_version", payload_version)
            signals.append(signal)
    return signals


def _earliest_unique(signals: list[dict]) -> list[dict]:
    selected = {}
    for signal in signals:
        trade_date, code = signal.get("trade_date"), signal.get("code")
        if not trade_date or not code:
            continue
        key = (trade_date, code)
        if key not in selected or str(signal.get("generated_at") or "") < str(selected[key].get("generated_at") or ""):
            selected[key] = signal
    return list(selected.values())


def load_unique_stock_days(research_dir: Path) -> list[dict]:
    """Use the earliest fully-scored observation per trade date and stock."""
    return _earliest_unique(_load_signals(research_dir))


def validate_strategy(
    research_dir: Path,
    minimum_samples: int = 100,
    round_trip_cost_bps: float = 20,
    strategy_version: str | None = None,
) -> dict:
    all_signals = _load_signals(research_dir, strategy_version)
    signals = _earliest_unique(all_signals)
    by_band = {band: [] for band in ("80-100", "70-79", "60-69", "0-59")}
    for signal in signals:
        by_band[_score_band(signal.get("score"))].append(signal)

    score_bands = {
        band: {
            "unique_stock_days": len(items),
            "next_open": _metrics(items, "next_open", round_trip_cost_bps),
            "next_1000": _metrics(items, "next_1000", round_trip_cost_bps),
            "day3_close": _metrics(items, "day3_close", round_trip_cost_bps),
            "day5_close": _metrics(items, "day5_close", round_trip_cost_bps),
        }
        for band, items in by_band.items()
    }

    def channel_items(channel: str, state: str) -> list[dict]:
        matching = [
            signal
            for signal in all_signals
            if bool(((signal.get("channels") or {}).get(channel) or {}).get(state))
        ]
        return _earliest_unique(matching)

    channel_results = {}
    for channel, labels in {
        "ordinary": ("next_open", "next_1000"),
        "hot": ("day3_close", "day5_close", "mfe_5d", "mae_5d"),
    }.items():
        channel_results[channel] = {}
        for state in ("qualified", "actionable_now"):
            items = channel_items(channel, state)
            outcomes = {label: _metrics(items, label, round_trip_cost_bps) for label in labels}
            channel_results[channel][state] = {
                "unique_stock_days": len(items),
                "status": "READY" if outcomes[labels[0]]["observations"] >= minimum_samples else "DATA_INSUFFICIENT",
                "outcomes": outcomes,
            }

    observed_next = sum(
        1
        for signal in signals
        if _observed_return(signal, "next_open") is not None or _observed_return(signal, "day3_close") is not None
    )
    score_status = "READY" if observed_next >= minimum_samples else "DATA_INSUFFICIENT"
    required_channel_observations = {
        "ordinary_actionable_next_open": channel_results["ordinary"]["actionable_now"]["outcomes"]["next_open"]["observations"],
        "hot_actionable_day3_close": channel_results["hot"]["actionable_now"]["outcomes"]["day3_close"]["observations"],
    }
    strategy_ready = all(value >= minimum_samples for value in required_channel_observations.values())
    return {
        "status": "READY" if strategy_ready else "DATA_INSUFFICIENT",
        "strategy_version": strategy_version,
        "minimum_unique_stock_days": minimum_samples,
        "observed_unique_stock_days": observed_next,
        "total_unique_stock_days": len(signals),
        "score_calibration_status": score_status,
        "required_channel_observations": required_channel_observations,
        "method": "仅使用同一策略版本；每个交易日每只股票只保留最早完整评分；普通与热点可执行通道分别达到样本门槛后才通过；收益扣除统一往返成本代理，结果不回写评分。",
        "score_bands": score_bands,
        "channels": channel_results,
    }


def dumps_validation(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
