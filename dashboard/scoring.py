from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean, median

from .data import TZ, number
from .provenance import parse_timestamp


MODULE_LABELS = {
    "A": "当日量价",
    "B": "尾盘动能",
    "C": "隔夜质量",
    "D": "板块持续",
    "E": "公告催化",
    "G": "反转风险",
}


def pct(current, previous):
    if number(current) is None or number(previous) in (None, 0):
        return None
    return (float(current) / float(previous) - 1) * 100


def clamp(value, low, high):
    return max(low, min(high, value))


def _avg(values):
    clean = [float(value) for value in values if number(value) is not None]
    return mean(clean) if clean else None


def _round(value, digits=2):
    return round(float(value), digits) if number(value) is not None else None


def trading_progress(timestamp) -> float:
    """Return the completed fraction of the 240-minute A-share session."""
    parsed = parse_timestamp(timestamp)
    if not parsed:
        return 0.0
    minute = parsed.hour * 60 + parsed.minute
    if minute < 9 * 60 + 30:
        elapsed = 0
    elif minute <= 11 * 60 + 30:
        elapsed = minute - (9 * 60 + 30)
    elif minute < 13 * 60:
        elapsed = 120
    elif minute <= 15 * 60:
        elapsed = 120 + minute - 13 * 60
    else:
        elapsed = 240
    return clamp(elapsed / 240, 0, 1)


def _weekday_gap(start_date: str | None, end_date: str | None) -> int | None:
    try:
        cursor = datetime.fromisoformat(str(start_date)).date()
        end = datetime.fromisoformat(str(end_date)).date()
    except (TypeError, ValueError):
        return None
    count = 0
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _same_time_volume_ratio(minutes: list[dict], trade_date: str) -> tuple[float | None, int]:
    today = sorted((row for row in minutes if str(row.get("time", ""))[:10] == trade_date), key=lambda row: row["time"])
    if not today:
        return None, 0
    latest = parse_timestamp(today[-1].get("time"))
    if not latest:
        return None, 0
    cutoff = (latest.hour, latest.minute)
    expected_bars = max(1, round(trading_progress(latest) * 240))
    if len(today) < max(10, round(expected_bars * 0.8)):
        return None, 0
    today_volume = sum(number(row.get("volume")) or 0 for row in today)
    prior_by_date = {}
    for row in minutes:
        stamp = parse_timestamp(row.get("time"))
        if not stamp or stamp.date().isoformat() == trade_date or (stamp.hour, stamp.minute) > cutoff:
            continue
        prior_by_date.setdefault(stamp.date().isoformat(), []).append(row)
    comparable = []
    for rows in prior_by_date.values():
        if len(rows) < max(10, round(expected_bars * 0.8)):
            continue
        comparable.append(sum(number(row.get("volume")) or 0 for row in rows))
    baseline = _avg(comparable)
    if not baseline:
        return None, 0
    return today_volume / baseline, len(comparable)


def market_summary(rows: list[dict], previous: dict | None, cfg: dict) -> tuple[dict, dict]:
    valid = [row for row in rows if number(row.get("change_pct")) is not None]
    changes = [row["change_pct"] for row in valid]
    up = sum(value > 0 for value in changes)
    down = sum(value < 0 for value in changes)
    flat = len(changes) - up - down
    breadth = up / max(up + down, 1)
    mid = median(changes) if changes else None
    limit_up = sum(value >= 9.5 for value in changes)
    limit_down = sum(value <= -9.5 for value in changes)
    total_amount = sum(number(row.get("amount")) or 0 for row in valid)

    by_sector = {}
    for row in valid:
        sector = row.get("industry")
        if sector:
            by_sector.setdefault(sector, []).append(row)
    current_sectors = {}
    sector_amounts = []
    for sector, members in by_sector.items():
        if len(members) < cfg["market"]["sector_members_min"]:
            continue
        values = [item["change_pct"] for item in members]
        sector_mid = median(values)
        sector_breadth = sum(value > 0 for value in values) / len(values)
        amount = sum(number(item.get("amount")) or 0 for item in members)
        relative = sector_mid - (mid or 0)
        item = {
            "sector": sector,
            "members": len(members),
            "median_pct": sector_mid,
            "breadth": sector_breadth,
            "amount": amount,
            "limit_up_count": sum(value >= 9.5 for value in values),
            "relative_strength": relative,
            "strong": sector_mid > 0
            and relative >= cfg["market"]["sector_rs_min"]
            and sector_breadth >= cfg["market"]["sector_breadth_min"],
        }
        current_sectors[sector] = item
        sector_amounts.append(amount)

    previous_state = ((previous or {}).get("diagnostics") or {}).get("sector_continuity") or []
    previous_map = {item["sector"]: item for item in (previous_state or (previous or {}).get("sectors") or [])}
    previous_time = (previous or {}).get("generated_at")
    current_trade_date = max((str(row.get("market_time"))[:10] for row in valid if row.get("market_time")), default=None)
    continuity_available = False
    if previous_time:
        try:
            current_time = max(
                (datetime.fromisoformat(str(row["market_time"])) for row in valid if row.get("market_time")),
                default=datetime.now(TZ),
            )
            age = current_time - datetime.fromisoformat(previous_time)
            continuity_available = 0 <= age.total_seconds() <= 14 * 86400
        except ValueError:
            pass
    for sector, item in current_sectors.items():
        prior = previous_map.get(sector) if continuity_available else None
        prior_trade_date = (prior or {}).get("last_trade_date") or (previous or {}).get("trade_date")
        prior_strong = bool(prior and prior.get("strong"))
        same_trade_date = bool(prior_trade_date and current_trade_date and prior_trade_date == current_trade_date)
        adjacent_trade_day = _weekday_gap(prior_trade_date, current_trade_date) == 1
        snapshot_streak = (
            int((prior or {}).get("snapshot_streak") or 0) + 1
            if item["strong"] and prior_strong and same_trade_date
            else 1 if item["strong"] else 0
        )
        prior_days = int((prior or {}).get("strong_trade_days") or (1 if prior_strong else 0))
        if not item["strong"]:
            strong_trade_days = 0
        elif prior_strong and prior_trade_date == current_trade_date:
            strong_trade_days = max(1, prior_days)
        elif prior_strong and adjacent_trade_day:
            strong_trade_days = max(1, prior_days) + 1
        else:
            strong_trade_days = 1
        item["continuity_available"] = continuity_available
        item["previous_strong"] = prior_strong
        item["snapshot_streak"] = snapshot_streak
        item["strong_trade_days"] = strong_trade_days
        item["confirmed_intraday"] = bool(item["strong"] and snapshot_streak >= 2)
        item["confirmed_swing"] = bool(item["strong"] and strong_trade_days >= 2)
        item["confirmed"] = item["confirmed_intraday"]
        item["last_trade_date"] = current_trade_date
        item["previous_median_pct"] = prior.get("median_pct") if prior else None
        if sector_amounts:
            item["amount_percentile"] = sum(value <= item["amount"] for value in sector_amounts) / len(sector_amounts)
        else:
            item["amount_percentile"] = None

    strong_count = sum(item["strong"] for item in current_sectors.values())
    environment_score = (
        (breadth - 0.5) * 2
        + (mid or 0) / 3
        + clamp((limit_up - limit_down) / 50, -0.5, 0.5)
        + min(strong_count / 20, 0.2)
    )
    if environment_score >= cfg["market"]["risk_on_score"]:
        regime = "RISK_ON"
    elif environment_score <= cfg["market"]["risk_off_score"]:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"
    market_times = sorted(row["market_time"] for row in valid if row.get("market_time"))
    summary = {
        "regime": regime,
        "environment_score": _round(environment_score, 3),
        "breadth": _round(breadth, 4),
        "advancers": up,
        "decliners": down,
        "flat": flat,
        "median_pct": _round(mid),
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "market_amount": total_amount,
        "market_time": market_times[-1] if market_times else None,
        "sector_coverage": _round(sum(bool(row.get("industry")) for row in valid) / max(len(valid), 1), 4),
        "continuity_available": continuity_available,
        "strong_sector_count": strong_count,
    }
    sectors = sorted(current_sectors.values(), key=lambda item: (-item["relative_strength"], -item["breadth"], -item["amount"]))
    return summary, {"items": current_sectors, "public": sectors[:20]}


def build_leader_board(rows: list[dict], sectors: dict, cfg: dict) -> dict:
    """Build a standalone board of leaders inside confirmed strong sectors.

    This board is deliberately independent from the A/B/C/D/E/G trade score. It
    uses the full quote snapshot so the label means sector-wide leader rather
    than leader among the detailed-score candidates only.
    """
    leader_cfg = cfg.get("leader_board") or {}
    if not leader_cfg.get("enabled", True):
        return {"status": "DISABLED", "sectors": [], "message": "板块龙头模块未启用"}

    grouped = {}
    for row in rows:
        sector_name = row.get("industry")
        if not sector_name or any(number(row.get(key)) is None for key in ("change_pct", "amount", "turnover_rate")):
            continue
        grouped.setdefault(sector_name, []).append(row)

    minimum_members = int(leader_cfg.get("minimum_sector_members", cfg.get("market", {}).get("sector_members_min", 5)))
    require_confirmed = bool(leader_cfg.get("require_confirmed", True))
    sector_limit = max(1, int(leader_cfg.get("sector_limit", 5)))
    leader_count = max(1, int(leader_cfg.get("leaders_per_sector", 2)))
    eligible_sectors = []
    for sector_name, sector in sectors.items():
        members = grouped.get(sector_name) or []
        if len(members) < minimum_members or not sector.get("strong"):
            continue
        if require_confirmed and not sector.get("confirmed"):
            continue
        eligible_sectors.append((sector_name, sector, members))

    eligible_sectors.sort(
        key=lambda item: (
            -float(item[1].get("relative_strength") or 0),
            -float(item[1].get("breadth") or 0),
            -float(item[1].get("amount") or 0),
            item[0],
        )
    )
    if not eligible_sectors:
        return {
            "status": "NO_CONFIRMED_SECTOR",
            "sectors": [],
            "message": "当前没有同时满足强度、广度和连续性确认的板块，暂不确认板块龙头。",
            "require_confirmed": require_confirmed,
        }

    output = []
    for sector_rank, (sector_name, sector, members) in enumerate(eligible_sectors[:sector_limit], 1):
        ordered_change = sorted(members, key=lambda row: (-float(row["change_pct"]), -float(row["amount"]), row["code"]))
        ordered_amount = sorted(members, key=lambda row: (-float(row["amount"]), -float(row["change_pct"]), row["code"]))
        max_change = max(float(row["change_pct"]) for row in members)
        min_change = min(float(row["change_pct"]) for row in members)
        change_span = max(max_change - min_change, 1.0)
        scored = []
        for change_rank, row in enumerate(ordered_change, 1):
            amount_rank = next(index for index, item in enumerate(ordered_amount, 1) if item["code"] == row["code"])
            rank_score = 100 * (len(members) - change_rank) / max(len(members) - 1, 1)
            amount_score = 100 * (len(members) - amount_rank) / max(len(members) - 1, 1)
            relative = float(row["change_pct"]) - float(sector.get("median_pct") or 0)
            relative_score = clamp(50 + relative * 20, 0, 100)
            turnover = float(row["turnover_rate"])
            turnover_score = 100 if 0.5 <= turnover <= 12 else 70 if turnover <= 20 else 25
            leader_score = round(relative_score * 0.45 + rank_score * 0.25 + amount_score * 0.20 + turnover_score * 0.10)
            scored.append(
                {
                    "row": row,
                    "score": leader_score,
                    "change_rank": change_rank,
                    "amount_rank": amount_rank,
                    "relative": relative,
                    "relative_score": relative_score,
                    "amount_score": amount_score,
                    "turnover_score": turnover_score,
                }
            )
        scored.sort(key=lambda item: (-item["score"], -float(item["row"]["amount"]), item["row"]["code"]))
        leader = scored[0]
        capacity = next((item for item in scored if item["row"]["code"] == ordered_amount[0]["code"]), leader)

        def public_item(item, role: str) -> dict:
            row = item["row"]
            risks = []
            if float(row["change_pct"]) >= 8.5:
                risks.append("接近涨停，追高风险")
            if float(row["turnover_rate"]) > float(cfg.get("scoring", {}).get("hot_turnover_max", 25)):
                risks.append("换手率过高")
            evidence = [
                f"板块内涨幅排名 {item['change_rank']}/{len(members)}｜相对板块中位 {item['relative']:+.2f}个百分点",
                f"成交额排名 {item['amount_rank']}/{len(members)}｜换手 {float(row['turnover_rate']):.2f}%",
            ]
            return {
                "code": row["code"],
                "name": row["name"],
                "role": role,
                "leader_score": item["score"],
                "change_rank": item["change_rank"],
                "amount_rank": item["amount_rank"],
                "sector_members": len(members),
                "change_pct": _round(row["change_pct"]),
                "amount": row["amount"],
                "turnover_rate": _round(row["turnover_rate"]),
                "relative_strength": _round(item["relative"]),
                "evidence": evidence,
                "risks": risks,
            }

        entries = [public_item(leader, "龙头")]
        if capacity["row"]["code"] != leader["row"]["code"] and leader_count > 1:
            entries.append(public_item(capacity, "容量核心"))
        output.append(
            {
                "sector": sector_name,
                "sector_rank": sector_rank,
                "sector_strength": _round(float(sector.get("relative_strength") or 0) * 20 + float(sector.get("breadth") or 0) * 50),
                "relative_strength": _round(sector.get("relative_strength")),
                "breadth": _round(sector.get("breadth"), 4),
                "members": len(members),
                "limit_up_count": sector.get("limit_up_count", 0),
                "confirmed": bool(sector.get("confirmed")),
                "leaders": entries[:leader_count],
                "message": "板块已连续确认，展示板块内相对强度与容量核心。",
                "quote_coverage": 1.0,
                "change_span": _round(change_span),
            }
        )
    return {
        "status": "SUCCESS",
        "sectors": output,
        "require_confirmed": require_confirmed,
        "message": "板块龙头独立展示，不计入A/B/C/D/E/G总评分。",
    }


def prefilter(rows: list[dict], sectors: dict, cfg: dict) -> tuple[list[dict], dict]:
    limits = cfg["universe"]
    counters = {"input": len(rows), "invalid_quote": 0, "liquidity": 0, "price_move": 0, "passed": 0}
    eligible = []
    for row in rows:
        required = (row.get("price"), row.get("change_pct"), row.get("amount"), row.get("turnover_rate"))
        if any(number(value) is None for value in required) or row["price"] <= 0 or row["amount"] <= 0:
            counters["invalid_quote"] += 1
            continue
        progress_floor = float((cfg.get("execution") or {}).get("minimum_intraday_progress", 0.15))
        amount_threshold = limits["minimum_amount"] * max(progress_floor, trading_progress(row.get("market_time")))
        if row["amount"] < amount_threshold or row["turnover_rate"] < limits["minimum_turnover_rate"]:
            counters["liquidity"] += 1
            continue
        if not limits["minimum_daily_change_pct"] <= row["change_pct"] <= limits["maximum_daily_change_pct"]:
            counters["price_move"] += 1
            continue
        sector = sectors.get(row.get("industry")) or {}
        copy = dict(row)
        copy["prefilter_score"] = (
            clamp(math.log10(max(row["amount"], 1)) - 8, 0, 2) * 0.22
            + clamp(row["turnover_rate"] / 10, 0, 1) * 0.18
            + clamp((row["change_pct"] + 1) / 8, 0, 1) * 0.22
            + clamp((sector.get("relative_strength") or 0) / 3, -0.3, 1) * 0.25
            + (0.13 if sector.get("confirmed") else 0.05 if sector.get("strong") else 0)
        )
        eligible.append(copy)
    eligible.sort(key=lambda item: (-item["prefilter_score"], -item["amount"], item["code"]))
    counters["passed"] = len(eligible)
    return eligible[: limits["prefilter_limit"]], counters


def technical_features(quote: dict, detail: dict, trade_date: str) -> dict:
    daily = [row for row in detail.get("daily", []) if row.get("date") <= trade_date]
    if len(daily) < 21 or daily[-1].get("date") != trade_date:
        return {"complete": False, "reason": "日K日期与行情日不一致或不足21根"}
    close = [row["close"] for row in daily]
    volume = [row["volume"] for row in daily]
    last = daily[-1]
    ma5 = mean(close[-5:])
    ma10 = mean(close[-10:])
    ma20 = mean(close[-20:])
    prior_vol5 = _avg(volume[-6:-1])
    raw_volume_ratio = volume[-1] / prior_vol5 if prior_vol5 else None
    high20_prior = max(row["high"] for row in daily[-21:-1])
    true_ranges = []
    for index in range(len(daily) - 20, len(daily)):
        previous_close = daily[index - 1]["close"]
        true_ranges.append(max(daily[index]["high"] - daily[index]["low"], abs(daily[index]["high"] - previous_close), abs(daily[index]["low"] - previous_close)))
    positive_bodies = [max(row["close"] - row["open"], 0) for row in daily[-20:]]
    negative_bodies = [max(row["open"] - row["close"], 0) for row in daily[-20:]]
    red = _avg([value for value in positive_bodies if value > 0]) or 0
    green = _avg([value for value in negative_bodies if value > 0]) or 0
    red_green_ratio = red / green if green else (3.0 if red else 1.0)
    limit_up_history = 0
    for index in range(max(1, len(daily) - 60), len(daily)):
        if pct(daily[index]["close"], daily[index - 1]["close"]) >= 9.5:
            limit_up_history += 1
    price = quote["price"]
    day_range = max((quote.get("high") or last["high"]) - (quote.get("low") or last["low"]), 0)
    low = quote.get("low") or last["low"]
    high = quote.get("high") or last["high"]
    open_price = quote.get("open") or last["open"]
    intraday_strength = (price - low) / day_range if day_range else 0.5
    upper_shadow = (high - max(open_price, price)) / day_range if day_range else 0

    all_minutes = detail.get("minute", [])
    today_minutes = [row for row in all_minutes if row.get("time", "")[:10] == trade_date]
    same_time_ratio, same_time_days = _same_time_volume_ratio(all_minutes, trade_date)
    progress = trading_progress(today_minutes[-1].get("time") if today_minutes else quote.get("market_time"))
    projected_ratio = raw_volume_ratio / max(progress, 0.15) if raw_volume_ratio is not None else None
    volume_ratio = same_time_ratio if same_time_ratio is not None else projected_ratio
    volume_ratio_method = "same_time_minutes" if same_time_ratio is not None else "progress_projection" if projected_ratio is not None else "unavailable"
    tail = today_minutes[-30:]
    intraday_vwap = None
    if today_minutes:
        total_volume = sum(row["volume"] for row in today_minutes)
        if total_volume:
            intraday_vwap = sum(row["close"] * row["volume"] for row in today_minutes) / total_volume
    tail_metrics = None
    if len(tail) >= 10:
        tail_low = min(row["low"] for row in tail)
        tail_high = max(row["high"] for row in tail)
        tail_range = tail_high - tail_low
        prior_tail = today_minutes[-60:-30]
        recent_volume = _avg([row["volume"] for row in tail[-5:]]) or 0
        prior_volume = _avg([row["volume"] for row in (prior_tail or tail[:-5])]) or 0
        tail_metrics = {
            "start": tail[0]["time"],
            "end": tail[-1]["time"],
            "return_pct": pct(tail[-1]["close"], tail[0]["open"]),
            "high_position": (tail[-1]["close"] - tail_low) / tail_range if tail_range else 0.5,
            "volume_ratio": recent_volume / prior_volume if prior_volume else None,
            "reversal_ratio": (tail_high - tail[-1]["close"]) / tail_range if tail_range else 0,
            "last_close": tail[-1]["close"],
        }
    return {
        "complete": True,
        "kline_date": daily[-1]["date"],
        "return_1d": pct(close[-1], close[-2]),
        "return_5d": pct(close[-1], close[-6]),
        "return_10d": pct(close[-1], close[-11]),
        "return_20d": pct(close[-1], close[-21]),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "distance_ma5_pct": pct(price, ma5),
        "volume_ratio": volume_ratio,
        "raw_volume_ratio": raw_volume_ratio,
        "volume_ratio_method": volume_ratio_method,
        "same_time_volume_days": same_time_days,
        "trading_progress": progress,
        "red_green_body_ratio": red_green_ratio,
        "limit_up_history_60d": limit_up_history,
        "high20_prior": high20_prior,
        "breakout": price > high20_prior,
        "near_breakout": price >= high20_prior * 0.98,
        "atr20": mean(true_ranges),
        "intraday_strength": intraday_strength,
        "upper_shadow_ratio": upper_shadow,
        "amplitude_pct": pct(high, low),
        "gap_pct": pct(open_price, quote.get("previous_close")),
        "intraday_vwap_approx": intraday_vwap,
        "minute_count": len(today_minutes),
        "tail": tail_metrics,
    }


def _module(key, score, state, evidence):
    maximum = {"A": 20, "B": 15, "C": 15, "D": 20, "E": 10, "G": 20}[key]
    if state == "数据不足":
        score = min(score, {"A": 10, "B": 8, "C": 8, "D": 10, "E": 5, "G": 10}[key])
    elif state == "不通过":
        score = min(score, math.floor(maximum * 0.4))
    return {"key": key, "label": MODULE_LABELS[key], "score": int(round(score)), "max": maximum, "state": state, "evidence": evidence}


def score_a(q, f, cfg):
    if not f.get("complete"):
        return _module("A", 0, "数据不足", [f.get("reason", "量价数据缺失")])
    score, evidence = 0, []
    change = q["change_pct"]
    if 1 <= change <= 7:
        score += 4
    elif 0 <= change <= 9:
        score += 2
    evidence.append(f"当日涨跌 {change:+.2f}%")
    strength = f["intraday_strength"]
    score += 4 if strength >= 0.75 else 2 if strength >= 0.60 else 0
    evidence.append(f"收盘位于日内区间 {strength:.0%}")
    vr = f.get("volume_ratio")
    if vr is not None:
        healthy_min = cfg["scoring"]["volume_ratio_healthy_min"]
        healthy_max = cfg["scoring"]["volume_ratio_healthy_max"]
        score += 4 if healthy_min <= vr <= healthy_max else 2 if 0.9 <= vr <= 3.5 else 0
        method = "历史同刻分钟量" if f.get("volume_ratio_method") == "same_time_minutes" else "按交易进度折算"
        evidence.append(f"盘中量比 {vr:.2f}（{method}）")
    rg = f["red_green_body_ratio"]
    score += 4 if rg >= cfg["scoring"]["red_green_body_ratio_pass"] else 2 if rg >= 1 else 0
    evidence.append(f"近20日阳/阴实体比 {rg:.2f}，60日涨停 {f['limit_up_history_60d']} 次")
    if f["breakout"] and q["price"] > f["ma5"]:
        score += 4
    elif f["near_breakout"] or q["price"] > f["ma5"]:
        score += 2
    evidence.append("突破20日前高" if f["breakout"] else "接近20日前高" if f["near_breakout"] else "未接近20日前高")
    return _module("A", score, "通过" if score >= cfg["scoring"]["a_pass"] else "不通过", evidence)


def score_b(q, f, cfg, now=None):
    phase = phase_at(now or datetime.now(TZ))
    if phase["code"] not in ("TAIL", "CLOSED"):
        return _module("B", 0, "数据不足", ["14:20前不评估尾盘动能，等待尾盘窗口确认"])
    tail = f.get("tail") if f.get("complete") else None
    if not tail:
        return _module("B", 8, "数据不足", ["当前时点没有至少10根当日分钟线，尾盘确认不可用"])
    score, evidence = 0, []
    ret = tail["return_pct"] or 0
    score += 4 if ret >= cfg["scoring"]["tail_return_pass"] else 2 if ret > 0 else 0
    score += 3 if tail["high_position"] >= cfg["scoring"]["tail_high_position_pass"] else 1 if tail["high_position"] >= 0.6 else 0
    tvr = tail.get("volume_ratio")
    if tvr is not None:
        score += 3 if tvr >= 1.2 else 1 if tvr >= 0.8 else 0
    vwap = f.get("intraday_vwap_approx")
    if vwap is not None:
        score += 3 if q["price"] >= vwap else 0
    score += 2 if tail["reversal_ratio"] <= 0.25 else 0
    evidence.append(f"最近30根分钟线 {tail['return_pct']:+.2f}%｜收在区间 {tail['high_position']:.0%}")
    evidence.append(f"末5分钟量能比 {tvr:.2f}" if tvr is not None else "末段量能无可比基准")
    evidence.append(f"现价 {'高于' if vwap and q['price'] >= vwap else '低于'}日内近似VWAP" if vwap else "日内VWAP不可用")
    return _module("B", score, "通过" if score >= cfg["scoring"]["b_pass"] else "不通过", evidence)


def score_c(q, f, cfg):
    if not f.get("complete"):
        return _module("C", 0, "数据不足", [f.get("reason", "隔夜质量数据缺失")])
    score, evidence = 0, []
    strength = f["intraday_strength"]
    score += 4 if strength >= 0.75 else 2 if strength >= 0.6 else 0
    amplitude = abs(f.get("amplitude_pct") or 0)
    score += 3 if 2 <= amplitude <= 8 else 1 if amplitude <= 10 else 0
    gap = abs(f.get("gap_pct") or 0)
    score += 2 if gap <= 3 else 0
    amount = q["amount"]
    score += 3 if amount >= 500_000_000 else 2 if amount >= 200_000_000 else 0
    upper = f["upper_shadow_ratio"]
    score += 3 if upper <= 0.25 else 1 if upper <= 0.45 else 0
    evidence.extend([f"日内振幅 {amplitude:.2f}%｜跳空 {gap:.2f}%", f"成交额 {amount / 100_000_000:.1f}亿｜上影占比 {upper:.0%}"])
    return _module("C", score, "通过" if score >= cfg["scoring"]["c_pass"] else "不通过", evidence)


def score_d(sector, cfg):
    if not sector:
        return _module("D", 0, "数据不足", ["缺少可靠板块归属或板块行情"])
    score, evidence = 0, []
    rs = sector["relative_strength"]
    score += 6 if rs >= 1.5 else 4 if rs >= 0.5 else 1 if rs > 0 else 0
    breadth = sector["breadth"]
    score += 5 if breadth >= 0.70 else 3 if breadth >= 0.60 else 0
    zt = sector["limit_up_count"]
    score += 3 if zt >= 3 else 2 if zt >= 1 else 0
    percentile = sector.get("amount_percentile")
    score += 2 if percentile is not None and percentile >= 0.75 else 1 if percentile is not None and percentile >= 0.5 else 0
    score += 1 if sector["median_pct"] > 0 else 0
    evidence.append(f"板块中位涨幅 {sector['median_pct']:+.2f}%｜相对全市场 {rs:+.2f}pct")
    evidence.append(f"上涨占比 {breadth:.0%}｜涨停 {zt}｜样本 {sector['members']}")
    swing_confirmed = sector.get("confirmed_swing", sector.get("confirmed", False))
    if not swing_confirmed:
        evidence.append(
            f"盘中连续 {int(sector.get('snapshot_streak') or 1)} 个节点｜跨日强势 {int(sector.get('strong_trade_days') or 1)} 个交易日，尚未达到热点波段确认"
        )
        return _module("D", min(score, 10), "数据不足", evidence)
    score += 3
    evidence.append(
        f"盘中连续 {int(sector.get('snapshot_streak') or 0)} 个节点｜跨日强势 {int(sector.get('strong_trade_days') or 0)} 个交易日"
    )
    state = "通过" if score >= cfg["scoring"]["d_pass"] and swing_confirmed else "不通过"
    return _module("D", score, state, evidence)


POSITIVE_WORDS = ("增持", "回购", "中标", "签订", "预增", "扭亏", "获批", "投产", "重大合同", "分红")
NEGATIVE_WORDS = ("减持", "立案", "处罚", "风险提示", "终止", "亏损", "退市", "诉讼", "冻结", "问询函")


def score_e(announcements, announcement_error=None, announcement_checked=True):
    if announcement_error:
        return _module("E", 0, "数据不足", ["全部公告源获取失败，本模块按0分计入但不暂停候选发布"])
    if not announcement_checked:
        return _module("E", 0, "数据不足", ["未进入公告复核范围，本模块不计分"])
    if not announcements:
        return _module("E", 5, "数据不足", ["近10日未检索到可作为催化的正式公告"])
    negative = [item for item in announcements if any(word in item["title"] for word in NEGATIVE_WORDS)]
    positive = [item for item in announcements if any(word in item["title"] for word in POSITIVE_WORDS)]
    if negative:
        return _module("E", 0, "不通过", [f"风险公告：{negative[0]['title']}"])
    if positive:
        title = positive[0]["title"]
        score = 8 if any(word in title for word in ("重大合同", "预增", "扭亏", "获批")) else 6
        return _module("E", score, "通过", [f"正式公告：{title}"])
    return _module("E", 5, "数据不足", [f"已核验 {len(announcements)} 条正式公告，未识别明确正向催化"])


def score_g(q, f, cfg):
    if not f.get("complete"):
        return _module("G", 0, "数据不足", [f.get("reason", "风险数据缺失")]), ["量价风险不可核验"]
    score, risks = 0, []
    turnover = q["turnover_rate"]
    score += 5 if 0.5 <= turnover <= 12 else 3 if turnover <= 20 else 0
    if turnover > cfg["scoring"]["hot_turnover_max"]:
        risks.append("换手率过高")
    upper = f["upper_shadow_ratio"]
    score += 5 if upper <= 0.25 else 3 if upper <= 0.45 else 0
    if upper >= 0.45 and (f.get("volume_ratio") or 0) >= 1.5:
        risks.append("放量长上影")
    amplitude = abs(f.get("amplitude_pct") or 0)
    score += 4 if amplitude <= 8 else 2 if amplitude <= 10 else 0
    if amplitude > cfg["scoring"]["hot_amplitude_max"]:
        risks.append("振幅过大")
    limit_up = q.get("previous_close") * 1.10 if q.get("previous_close") else None
    near_limit = bool(limit_up and q["price"] >= limit_up * cfg["scoring"]["near_limit_ratio"])
    score += 3 if not near_limit else 0
    if near_limit:
        risks.append("接近涨停，普通委托成交不确定")
    tail = f.get("tail")
    reversed_tail = bool(tail and tail["reversal_ratio"] > 0.45)
    score += 3 if not reversed_tail else 0
    if reversed_tail:
        risks.append("尾段冲高回落")
    if (f.get("return_5d") or 0) >= 18:
        risks.append("5日涨幅过热")
    if (f.get("return_10d") or 0) >= 28:
        risks.append("10日涨幅过热")
    if (f.get("distance_ma5_pct") or 0) >= 8:
        risks.append("明显偏离MA5")
    if (f.get("volume_ratio") or 0) >= 3.5:
        risks.append("异常巨量")
    evidence = [f"换手 {turnover:.2f}%｜振幅 {amplitude:.2f}%｜上影 {upper:.0%}"]
    evidence.append("未见主要反转风险" if not risks else "；".join(risks))
    state = "不通过" if risks else "通过" if score >= cfg["scoring"]["g_pass"] else "不通过"
    return _module("G", score, state, evidence), risks


def phase_at(now: datetime) -> dict:
    minute = now.hour * 60 + now.minute
    if minute < 9 * 60 + 25:
        return {"code": "PREOPEN", "label": "盘前·上一交易日收盘", "ordinary_open": False, "hot_open": False}
    if minute < 9 * 60 + 30:
        return {"code": "AUCTION", "label": "集合竞价", "ordinary_open": False, "hot_open": False}
    if minute <= 11 * 60 + 30:
        return {"code": "MORNING", "label": "上午盘", "ordinary_open": False, "hot_open": True}
    if minute < 13 * 60:
        return {"code": "LUNCH", "label": "午间", "ordinary_open": False, "hot_open": False}
    if minute < 14 * 60 + 20:
        return {"code": "AFTERNOON", "label": "午后盘", "ordinary_open": False, "hot_open": True}
    if minute < 15 * 60:
        return {"code": "TAIL", "label": "尾盘窗口", "ordinary_open": True, "hot_open": True}
    return {"code": "CLOSED", "label": "收盘复盘", "ordinary_open": False, "hot_open": False}


def source_quality(q, detail, cfg=None) -> dict:
    """Evaluate provenance health separately from field completeness."""
    config = (cfg or {}).get("data_quality") or {}
    fallback_penalty = float(config.get("fallback_penalty", 30))
    timestamp_penalty = float(config.get("timestamp_penalty", 15))
    alignment_penalty = float(config.get("alignment_penalty", 15))
    latency_warning = float(config.get("latency_warning_seconds", 120))
    latency_critical = max(latency_warning + 1, float(config.get("latency_critical_seconds", 300)))
    latency_penalty_max = float(config.get("latency_penalty_max", 25))
    provenance = detail.get("provenance") or {}
    metadata_present = any(
        value not in (None, "")
        for value in (
            q.get("data_source"),
            q.get("received_at"),
            q.get("timestamp_source"),
            provenance.get("daily_source"),
            provenance.get("minute_source"),
        )
    )
    if not metadata_present:
        return {
            "score": 100,
            "status": "未提供来源追踪，按兼容模式处理",
            "issues": [],
            "fallback": False,
            "max_latency_seconds": None,
            "alignment_seconds": None,
        }

    score = 100.0
    issues = []
    quote_fallback = bool(q.get("source_fallback"))
    detail_fallback = bool(provenance.get("fallback") or provenance.get("tdx_fallback"))
    if quote_fallback:
        score -= fallback_penalty
        issues.append(q.get("source_fallback_reason") or "报价使用回退来源")
    if detail_fallback:
        score -= fallback_penalty
        issues.append(provenance.get("fallback_reason") or "日K或分钟线使用回退来源")

    timestamp_source = str(q.get("timestamp_source") or "missing")
    if not q.get("market_time") or timestamp_source in {"missing", "fallback_quote"}:
        score -= timestamp_penalty
        issues.append("报价时间戳缺失或来自回退行情")

    latencies = [
        value
        for value in (q.get("source_latency_seconds"), provenance.get("minute_latency_seconds"))
        if isinstance(value, (int, float))
    ]
    max_latency = max(latencies) if latencies else None
    if max_latency is not None and max_latency > latency_warning:
        ratio = min(1.0, (max_latency - latency_warning) / (latency_critical - latency_warning))
        score -= latency_penalty_max * ratio
        issues.append(f"数据延迟 {max_latency:.1f} 秒")

    quote_time = parse_timestamp(q.get("market_time"))
    minute_time = parse_timestamp(provenance.get("minute_provider_time"))
    alignment_seconds = None
    if quote_time and minute_time:
        alignment_seconds = abs((quote_time - minute_time).total_seconds())
        alignment_warning = float(config.get("alignment_warning_seconds", 180))
        if alignment_seconds > alignment_warning:
            score -= alignment_penalty
            issues.append(f"报价与分钟线相差 {alignment_seconds:.0f} 秒")

    score = int(round(max(0.0, min(100.0, score))))
    return {
        "score": score,
        "status": "来源质量正常" if not issues else "来源质量存在问题",
        "issues": issues,
        "fallback": quote_fallback or detail_fallback,
        "max_latency_seconds": round(max_latency, 3) if max_latency is not None else None,
        "alignment_seconds": round(alignment_seconds, 3) if alignment_seconds is not None else None,
    }


def data_confidence(q, f, sector, announcement_error, announcement_checked, announcement_source=None, cfg=None, detail=None) -> dict:
    """Score source completeness separately from the trading score."""
    components = []

    def add(key, label, score, maximum, detail):
        components.append({"key": key, "label": label, "score": score, "max": maximum, "detail": detail})

    quote_ok = bool(q.get("market_time") and number(q.get("price")) is not None)
    add("quote", "行情时间与报价", 15 if quote_ok else 0, 15, "可核验" if quote_ok else "缺少时间戳或报价")

    daily_ok = bool(f.get("complete"))
    add("daily", "当日日K", 25 if daily_ok else 0, 25, "日期一致且不少于21根" if daily_ok else f.get("reason", "日K不完整"))

    minute_count = int(f.get("minute_count") or 0)
    minute_score = 20 if minute_count >= 30 else 12 if minute_count >= 10 else 0
    add("minute", "当日分钟线", minute_score, 20, f"可用{minute_count}根")

    sector_score = 0
    sector_detail = "缺少板块归属"
    if sector:
        sector_score = 10
        sector_detail = "板块归属可用"
        if sector.get("continuity_available"):
            sector_score = 20
            sector_detail = "板块归属与上一快照均可用"
    add("sector", "板块数据", sector_score, 20, sector_detail)

    if not announcement_checked:
        announcement_score, announcement_detail = 0, "未进入公告复核范围"
    elif announcement_error:
        announcement_score, announcement_detail = 0, "公告源全部失败"
    elif announcement_source and "备份" in announcement_source:
        announcement_score, announcement_detail = 15, f"已由{announcement_source}核验"
    else:
        announcement_score, announcement_detail = 20, f"已由{announcement_source or '公告源'}核验"
    add("announcement", "公告数据", announcement_score, 20, announcement_detail)

    completeness_score = sum(item["score"] for item in components)
    source = source_quality(q, detail or {}, cfg)
    source_weight = float(((cfg or {}).get("data_quality") or {}).get("source_weight", 0.30))
    source_weight = max(0.0, min(1.0, source_weight))
    score = int(round(completeness_score * (1 - source_weight) + source["score"] * source_weight))
    level = "高" if score >= 90 else "中" if score >= 70 else "低"
    issues = [item["detail"] for item in components if item["score"] < item["max"]]
    issues.extend(source["issues"])
    return {
        "score": score,
        "max": 100,
        "level": level,
        "components": components,
        "issues": issues,
        "completeness_score": completeness_score,
        "source_quality_score": source["score"],
        "source_quality": source,
        "source_weight": source_weight,
    }


def _channel_result(keys, module_map, hard_ok, extra_gate, phase_open, qualify_pct, reasons):
    score = sum(module_map[key]["score"] for key in keys)
    maximum = sum(module_map[key]["max"] for key in keys)
    normalized = round(score / maximum * 100, 1) if maximum else 0
    modules_pass = all(module_map[key]["state"] == "通过" for key in keys)
    qualified = bool(hard_ok and extra_gate and modules_pass and normalized >= qualify_pct)
    return {
        "qualified": qualified,
        "actionable_now": bool(qualified and phase_open),
        "score": score,
        "max": maximum,
        "normalized_score": normalized,
        "required_modules": list(keys),
        "reasons": reasons if not qualified else [],
    }


def execution_data_status(q, f, detail, evaluation_time, cfg) -> dict:
    limits = cfg.get("execution") or {}
    quote_time = parse_timestamp(q.get("market_time"))
    provenance = detail.get("provenance") or {}
    minute_time = parse_timestamp(provenance.get("minute_provider_time"))
    if minute_time is None:
        parsed_minutes = [parse_timestamp(row.get("time")) for row in detail.get("minute", [])]
        minute_time = max((stamp for stamp in parsed_minutes if stamp), default=None)
    reasons = []

    def age_seconds(stamp):
        return max(0.0, (evaluation_time - stamp.astimezone(evaluation_time.tzinfo)).total_seconds()) if stamp else None

    quote_age = age_seconds(quote_time)
    minute_age = age_seconds(minute_time)
    alignment = abs((quote_time - minute_time).total_seconds()) if quote_time and minute_time else None
    if quote_age is None:
        reasons.append("报价时间不可核验")
    elif quote_age > float(limits.get("max_quote_age_seconds", 180)):
        reasons.append(f"报价延迟 {quote_age:.0f} 秒")
    if minute_age is None:
        reasons.append("分钟线时间不可核验")
    elif minute_age > float(limits.get("max_minute_age_seconds", 360)):
        reasons.append(f"分钟线延迟 {minute_age:.0f} 秒")
    if alignment is not None and alignment > float(limits.get("max_quote_minute_alignment_seconds", 180)):
        reasons.append(f"报价与分钟线相差 {alignment:.0f} 秒")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "quote_age_seconds": _round(quote_age, 1),
        "minute_age_seconds": _round(minute_age, 1),
        "alignment_seconds": _round(alignment, 1),
    }


def finalize_action_state(channel: dict, phase_open: bool, triggered: bool, data_status: dict) -> None:
    if not channel["qualified"]:
        state, message = "UNQUALIFIED", "通道条件未通过"
    elif not phase_open:
        state, message = "WINDOW_CLOSED", "通道合格，等待执行窗口"
    elif not data_status["ready"]:
        state, message = "DATA_STALE", "通道合格，但行情新鲜度不足"
    elif not triggered:
        state, message = "WAIT_TRIGGER", "通道合格，价格尚未确认突破"
    else:
        state, message = "TRIGGERED", "通道合格，价格、窗口和数据均已确认"
    channel["action_state"] = state
    channel["action_message"] = message
    channel["actionable_now"] = state == "TRIGGERED"


def score_candidate(
    quote,
    detail,
    sector,
    market,
    cfg,
    trade_date,
    announcements=None,
    announcement_error=None,
    announcement_checked=True,
    announcement_source=None,
    now=None,
):
    evaluation_time = now or datetime.now(TZ)
    features = technical_features(quote, detail, trade_date)
    modules = [
        score_a(quote, features, cfg),
        score_b(quote, features, cfg, evaluation_time),
        score_c(quote, features, cfg),
        score_d(sector, cfg),
        score_e(announcements, announcement_error, announcement_checked),
    ]
    g, risks = score_g(quote, features, cfg)
    modules.append(g)
    total = sum(item["score"] for item in modules)
    if total >= cfg["levels"]["strong"]:
        level = "强条件候选"
    elif total >= cfg["levels"]["watch"]:
        level = "条件观察"
    elif total >= cfg["levels"]["weak"]:
        level = "弱观察"
    else:
        level = "排除"
    module_map = {item["key"]: item for item in modules}
    phase = phase_at(evaluation_time)
    support = max(features.get("ma5") or 0, (features.get("intraday_vwap_approx") or 0))
    atr = features.get("atr20") or quote["price"] * 0.02
    breakout_reference = features.get("high20_prior") or 0
    trigger = breakout_reference + 0.01
    invalid = max(0.01, min(support if support else quote["price"], quote["price"] - 0.5 * atr) - 0.01)
    no_chase = min((quote.get("previous_close") or quote["price"]) * 1.10 - 0.02, quote["price"] * 1.02)
    plan_feasible = trigger <= no_chase and invalid < trigger
    trigger_required = bool((cfg.get("execution") or {}).get("require_price_trigger", True))
    triggered_now = bool(
        not trigger_required
        or (
            plan_feasible
            and quote["price"] >= trigger
            and quote["price"] <= no_chase
            and (not support or quote["price"] >= support)
        )
    )
    execution_data = execution_data_status(quote, features, detail, evaluation_time, cfg)
    hard_ok = features.get("complete") and not risks and plan_feasible
    qualify_pct = cfg["scoring"].get("channel_qualify_pct", 60)
    common_reasons = []
    if not features.get("complete"):
        common_reasons.append("技术数据不完整")
    if risks:
        common_reasons.append("触发反转风险")
    if not plan_feasible:
        common_reasons.append("买点区间不可执行")
    ordinary_reasons = list(common_reasons)
    if market["regime"] == "RISK_OFF":
        ordinary_reasons.append("市场环境为防守")
    ordinary_failed = [key for key in ("A", "B", "C", "G") if module_map[key]["state"] != "通过"]
    if ordinary_failed:
        ordinary_reasons.append("必过模块未通过：" + "、".join(ordinary_failed))
    hot_reasons = list(common_reasons)
    hot_failed = [key for key in ("A", "D", "G") if module_map[key]["state"] != "通过"]
    if hot_failed:
        hot_reasons.append("必过模块未通过：" + "、".join(hot_failed))

    ordinary = _channel_result(
        ("A", "B", "C", "G"), module_map, hard_ok, market["regime"] != "RISK_OFF",
        phase["ordinary_open"], qualify_pct, ordinary_reasons,
    )
    ordinary["holding"] = "尾盘确认，次日早盘按条件退出"
    hot = _channel_result(
        ("A", "D", "G"), module_map, hard_ok, True,
        phase["hot_open"], qualify_pct, hot_reasons,
    )
    hot["holding"] = "计划2–5个交易日，退潮或失效提前退出"
    finalize_action_state(ordinary, phase["ordinary_open"], triggered_now, execution_data)
    finalize_action_state(hot, phase["hot_open"], triggered_now, execution_data)
    confidence = data_confidence(
        quote, features, sector, announcement_error, announcement_checked, announcement_source, cfg=cfg, detail=detail
    )
    detail_provenance = detail.get("provenance") or {}
    return {
        "code": quote["code"],
        "name": quote["name"],
        "sector": quote.get("industry") or "板块未知",
        "price": _round(quote["price"]),
        "change_pct": _round(quote["change_pct"]),
        "amount": quote["amount"],
        "turnover_rate": _round(quote["turnover_rate"]),
        "market_time": quote.get("market_time"),
        "data_provenance": {
            "quote_source": quote.get("data_source"),
            "quote_received_at": quote.get("received_at"),
            "quote_provider_time": quote.get("market_time"),
            "quote_timestamp_source": quote.get("timestamp_source"),
            "quote_latency_seconds": quote.get("source_latency_seconds"),
            "daily_source": detail_provenance.get("daily_source"),
            "minute_source": detail_provenance.get("minute_source"),
            "detail_received_at": detail_provenance.get("received_at"),
            "daily_provider_time": detail_provenance.get("daily_provider_time"),
            "minute_provider_time": detail_provenance.get("minute_provider_time"),
            "daily_latency_seconds": detail_provenance.get("daily_latency_seconds"),
            "minute_latency_seconds": detail_provenance.get("minute_latency_seconds"),
            "fallback": bool(detail_provenance.get("fallback")),
        },
        "score": total,
        "level": level,
        "modules": modules,
        "risks": risks,
        "data_confidence": confidence,
        "channels": {"ordinary": ordinary, "hot": hot},
        "plan": {
            "feasible": plan_feasible,
            "triggered_now": triggered_now,
            "trigger_status": "TRIGGERED" if triggered_now else "WAIT_TRIGGER" if plan_feasible else "NO_FEASIBLE_RANGE",
            "message": "价格已经确认突破且未超过不追价" if triggered_now else "存在可观察的触发区间，等待价格确认" if plan_feasible else "突破确认价高于不追价，当前没有可执行跟随区间",
            "hold_above": _round(support),
            "breakout_reference": _round(breakout_reference),
            "breakout": _round(trigger),
            "no_chase_above": _round(no_chase),
            "invalid_below": _round(invalid),
        },
        "execution_data": execution_data,
        "features": {
            "return_5d": _round(features.get("return_5d")),
            "volume_ratio": _round(features.get("volume_ratio")),
            "raw_volume_ratio": _round(features.get("raw_volume_ratio")),
            "volume_ratio_method": features.get("volume_ratio_method"),
            "trading_progress": _round(features.get("trading_progress"), 4),
            "red_green_body_ratio": _round(features.get("red_green_body_ratio")),
            "limit_up_history_60d": features.get("limit_up_history_60d"),
            "breakout": features.get("breakout"),
        },
        "announcements": (announcements or [])[:3],
    }
