from __future__ import annotations


REGIME_LABELS = {
    "RISK_ON": "偏进攻",
    "NEUTRAL": "中性震荡",
    "RISK_OFF": "偏防守",
}


def _pct(value, digits=1):
    if value is None:
        return "未知"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def _amount(value):
    if value is None:
        return "未知"
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}万亿元"
    return f"{value / 100_000_000:.0f}亿元"


def _market_text(payload):
    market = payload["market"]
    regime = REGIME_LABELS.get(market.get("regime"), "状态未知")
    breadth = (market.get("breadth") or 0) * 100
    return (
        f"{payload['phase']['label']}市场处于{regime}状态。主板上涨{market['advancers']}家、"
        f"下跌{market['decliners']}家，上涨占比{breadth:.1f}%；个股涨跌中位数"
        f"{_pct(market.get('median_pct'), 2)}。涨停{market['limit_up_count']}家、"
        f"跌停{market['limit_down_count']}家，当前统计成交额{_amount(market.get('market_amount'))}。"
    )


def _sector_text(payload):
    sectors = payload.get("sectors") or []
    confirmed = [item for item in sectors if item.get("strong") and item.get("confirmed")][:3]
    unconfirmed = [item for item in sectors if item.get("strong") and not item.get("confirmed")][:3]
    if confirmed:
        parts = [
            f"{item['sector']}（相对强度{item['relative_strength']:+.2f}个百分点、上涨占比{item['breadth'] * 100:.0f}%）"
            for item in confirmed
        ]
        return "连续性已经确认的强势方向为" + "、".join(parts) + "。优先观察其中有辨识度且评分通过的核心个股。"
    if unconfirmed:
        names = "、".join(item["sector"] for item in unconfirmed)
        return f"{names}当前相对强度靠前，但缺少上一有效快照的连续确认，暂按单节点异动观察。"
    return "当前没有达到强度和板块广度门槛的方向，资金方向尚未形成可确认的共振。"


def _strategy_text(payload):
    ordinary = payload["channels"]["ordinary"]
    hot = payload["channels"]["hot"]
    ordinary_state = "开启" if ordinary.get("open") else "关闭"
    hot_state = "开启" if hot.get("open") else "关闭"
    return (
        f"普通隔夜通道{ordinary_state}，合格候选{ordinary.get('qualified_count', 0)}只：{ordinary.get('message', '—')}。"
        f"热点波段通道{hot_state}，合格候选{hot.get('qualified_count', 0)}只：{hot.get('message', '—')}。"
    )


def _candidate_text(payload):
    candidates = payload.get("candidates") or []
    if not candidates:
        return "当前没有股票达到60分候选线。保持空候选，等待市场、板块、个股和买点重新共振。"
    top = candidates[0]
    passed = [item["key"] for item in top.get("modules", []) if item.get("state") == "通过"]
    blocked = [item["key"] for item in top.get("modules", []) if item.get("state") != "通过"]
    text = f"当前有{len(candidates)}只股票进入候选池，评分居前的是{top['name']}（{top['code']}）{top['score']}分。"
    if passed:
        text += f"已通过模块：{'、'.join(passed)}。"
    if blocked:
        text += f"仍需确认模块：{'、'.join(blocked)}。"
    if not top.get("plan", {}).get("feasible"):
        text += "其突破确认价与不追价区间不匹配，当前不具备可执行跟随区间。"
    else:
        text += "只有价格进入页面给出的确认区间，且对应通道处于执行窗口时，才继续观察。"
    return text


def _risk_text(payload):
    market = payload["market"]
    notes = []
    if market.get("regime") == "RISK_OFF":
        notes.append("市场环境处于防守状态，普通隔夜通道维持关闭")
    if not market.get("continuity_available"):
        notes.append("缺少可用的上一节点快照，板块连续性不能确认")
    if payload.get("diagnostics", {}).get("detail_errors"):
        notes.append("部分个股明细获取失败，相关股票未进入完整评分")
    if not notes:
        notes.append("当前未触发系统级数据降级，但个股仍须满足板块、量价、买点和风险模块")
    return "；".join(notes) + "。候选是条件化研究结果，不代表确定性收益。"


def build_analysis(payload):
    if payload.get("status") != "SUCCESS":
        error = payload.get("error") or "核心行情不完整"
        return {
            "mode": "规则模板",
            "summary": f"本次行情更新未通过完整性检查：{error}。系统已经停止筛选，没有沿用旧候选，也不生成盘面方向判断。",
            "sections": [],
        }
    sections = [
        {"label": "市场环境", "text": _market_text(payload)},
        {"label": "资金方向", "text": _sector_text(payload)},
        {"label": "策略状态", "text": _strategy_text(payload)},
        {"label": "候选解读", "text": _candidate_text(payload)},
        {"label": "风险提示", "text": _risk_text(payload)},
    ]
    return {
        "mode": "规则模板",
        "summary": " ".join(item["text"] for item in sections[:2]),
        "sections": sections,
    }
