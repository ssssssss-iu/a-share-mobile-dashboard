from __future__ import annotations

import math
from datetime import datetime
from statistics import mean, median

from .data import TZ, number


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

    previous_map = {item["sector"]: item for item in ((previous or {}).get("sectors") or [])}
    previous_time = (previous or {}).get("generated_at")
    continuity_available = False
    if previous_time:
        try:
            age = datetime.now(TZ) - datetime.fromisoformat(previous_time)
            continuity_available = 0 <= age.total_seconds() <= 4 * 86400
        except ValueError:
            pass
    for sector, item in current_sectors.items():
        prior = previous_map.get(sector) if continuity_available else None
        item["previous_strong"] = bool(prior and prior.get("strong"))
        item["confirmed"] = bool(item["strong"] and item["previous_strong"])
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


def prefilter(rows: list[dict], sectors: dict, cfg: dict) -> tuple[list[dict], dict]:
    limits = cfg["universe"]
    counters = {"input": len(rows), "invalid_quote": 0, "liquidity": 0, "price_move": 0, "passed": 0}
    eligible = []
    for row in rows:
        required = (row.get("price"), row.get("change_pct"), row.get("amount"), row.get("turnover_rate"))
        if any(number(value) is None for value in required) or row["price"] <= 0 or row["amount"] <= 0:
            counters["invalid_quote"] += 1
            continue
        if row["amount"] < limits["minimum_amount"] or row["turnover_rate"] < limits["minimum_turnover_rate"]:
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
    volume_ratio = volume[-1] / prior_vol5 if prior_vol5 else None
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

    today_minutes = [row for row in detail.get("minute", []) if row.get("time", "")[:10] == trade_date]
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
        score += 4 if 1.2 <= vr <= 2.8 else 2 if 0.9 <= vr <= 3.5 else 0
        evidence.append(f"量比(对近5日) {vr:.2f}")
    rg = f["red_green_body_ratio"]
    score += 4 if rg >= cfg["scoring"]["red_green_body_ratio_pass"] else 2 if rg >= 1 else 0
    evidence.append(f"近20日阳/阴实体比 {rg:.2f}，60日涨停 {f['limit_up_history_60d']} 次")
    if f["breakout"] and q["price"] > f["ma5"]:
        score += 4
    elif f["near_breakout"] or q["price"] > f["ma5"]:
        score += 2
    evidence.append("突破20日前高" if f["breakout"] else "接近20日前高" if f["near_breakout"] else "未接近20日前高")
    return _module("A", score, "通过" if score >= cfg["scoring"]["a_pass"] else "不通过", evidence)


def score_b(q, f, cfg):
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
    if not sector.get("previous_strong"):
        evidence.append("缺少上一有效快照的连续强势确认")
        return _module("D", min(score, 10), "数据不足", evidence)
    score += 3
    evidence.append("当前与上一有效快照均为强板块")
    state = "通过" if score >= cfg["scoring"]["d_pass"] and sector.get("confirmed") else "不通过"
    return _module("D", score, state, evidence)


POSITIVE_WORDS = ("增持", "回购", "中标", "签订", "预增", "扭亏", "获批", "投产", "重大合同", "分红")
NEGATIVE_WORDS = ("减持", "立案", "处罚", "风险提示", "终止", "亏损", "退市", "诉讼", "冻结", "问询函")


def score_e(announcements, announcement_error=None):
    if announcement_error:
        return _module("E", 0, "数据不足", ["巨潮公告获取失败，不能确认催化"])
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


def score_candidate(quote, detail, sector, market, cfg, trade_date, announcements=None, announcement_error=None, now=None):
    features = technical_features(quote, detail, trade_date)
    modules = [score_a(quote, features, cfg), score_b(quote, features, cfg), score_c(quote, features, cfg), score_d(sector, cfg), score_e(announcements, announcement_error)]
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
    phase = phase_at(now or datetime.now(TZ))
    support = max(features.get("ma5") or 0, (features.get("intraday_vwap_approx") or 0))
    high = quote.get("high") or quote["price"]
    atr = features.get("atr20") or quote["price"] * 0.02
    trigger = max(features.get("high20_prior") or 0, high) + 0.01
    invalid = max(0.01, min(support if support else quote["price"], quote["price"] - 0.5 * atr) - 0.01)
    no_chase = min((quote.get("previous_close") or quote["price"]) * 1.10 - 0.02, quote["price"] * 1.02)
    plan_feasible = trigger <= no_chase and invalid < trigger
    hard_ok = features.get("complete") and not risks and plan_feasible
    ordinary_gate = market["regime"] != "RISK_OFF" and all(module_map[key]["state"] == "通过" for key in ("A", "B", "C", "G"))
    hot_gate = all(module_map[key]["state"] == "通过" for key in ("A", "D", "G"))
    ordinary_qualified = bool(hard_ok and total >= 80 and ordinary_gate)
    hot_qualified = bool(hard_ok and total >= 80 and hot_gate)
    return {
        "code": quote["code"],
        "name": quote["name"],
        "sector": quote.get("industry") or "板块未知",
        "price": _round(quote["price"]),
        "change_pct": _round(quote["change_pct"]),
        "amount": quote["amount"],
        "turnover_rate": _round(quote["turnover_rate"]),
        "market_time": quote.get("market_time"),
        "score": total,
        "level": level,
        "modules": modules,
        "risks": risks,
        "channels": {
            "ordinary": {"qualified": ordinary_qualified, "actionable_now": ordinary_qualified and phase["ordinary_open"], "holding": "尾盘确认，次日早盘按条件退出"},
            "hot": {"qualified": hot_qualified, "actionable_now": hot_qualified and phase["hot_open"], "holding": "计划2–5个交易日，退潮或失效提前退出"},
        },
        "plan": {
            "feasible": plan_feasible,
            "message": "存在可观察的触发区间" if plan_feasible else "突破确认价高于不追价，当前没有可执行跟随区间",
            "hold_above": _round(support),
            "breakout": _round(trigger),
            "no_chase_above": _round(no_chase),
            "invalid_below": _round(invalid),
        },
        "features": {
            "return_5d": _round(features.get("return_5d")),
            "volume_ratio": _round(features.get("volume_ratio")),
            "red_green_body_ratio": _round(features.get("red_green_body_ratio")),
            "limit_up_history_60d": features.get("limit_up_history_60d"),
            "breakout": features.get("breakout"),
        },
        "announcements": (announcements or [])[:3],
    }
