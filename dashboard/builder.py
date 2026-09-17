from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .data import MarketClient, load_json
from .narrative import build_analysis
from .schedule import should_write_close_history
from .scoring import market_summary, phase_at, prefilter, score_candidate


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temp = Path(handle.name)
    os.replace(temp, path)


def safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:400]}"


def latest_successful_snapshot(previous: dict | None, history_dir: Path) -> dict | None:
    if previous and previous.get("status") == "SUCCESS":
        return previous
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        snapshot = load_json(path)
        if snapshot and snapshot.get("status") == "SUCCESS":
            return snapshot
    return None


def previous_close_snapshot(snapshot: dict, now: datetime) -> dict:
    trade_date = snapshot.get("trade_date")
    if not trade_date:
        raise RuntimeError("上一交易日收盘快照缺少交易日期")
    snapshot_date = date.fromisoformat(trade_date)
    age = (now.date() - snapshot_date).days
    if age <= 0 or age > 20:
        raise RuntimeError(f"上一交易日收盘快照日期异常: {trade_date}")

    result = deepcopy(snapshot)
    source_generated_at = result.get("generated_at")
    result["generated_at"] = now.isoformat(timespec="seconds")
    result["phase"] = phase_at(now)
    result["data_context"] = {
        "code": "PREVIOUS_CLOSE",
        "label": "上一交易日收盘",
        "trade_date": trade_date,
        "source_generated_at": source_generated_at,
        "message": "9:25竞价节点前展示最近一次成功收盘快照，不执行新筛选。",
    }
    for key in ("ordinary", "hot"):
        channel = result.get("channels", {}).get(key)
        if channel:
            channel["open"] = False
            channel["message"] = "9:25前仅展示上一交易日收盘"
    for collection in ("rankings", "candidates"):
        for candidate in result.get(collection) or []:
            for channel in (candidate.get("channels") or {}).values():
                channel["actionable_now"] = False
    result.setdefault("diagnostics", {})["previous_close_reused"] = True
    result["diagnostics"]["source_generated_at"] = source_generated_at
    result["analysis"] = build_analysis(result)
    return result


def build(output: Path | None = None, history_dir: Path | None = None, now: datetime | None = None) -> dict:
    output = output or ROOT / "site/latest.json"
    history_dir = history_dir or ROOT / "site/history"
    cfg = load_json(ROOT / "config/scoring_candidate.json")
    if not cfg:
        raise RuntimeError("评分配置无法读取")
    tz = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    previous = load_json(output)
    base = {
        "schema_version": "1.0",
        "status": "RUNNING",
        "generated_at": now.isoformat(timespec="seconds"),
        "phase": phase_at(now),
        "strategy": {
            "id": cfg["strategy_id"],
            "version": cfg["strategy_version"],
            "automation_profile": cfg["automation_profile"],
            "automation_status": cfg["automation_status"],
            "weights": cfg["weights"],
            "note": "模块权重来自生效策略；精确阈值是自动化试运行参数，尚未替代生效策略。",
        },
        "market": None,
        "indices": [],
        "sectors": [],
        "channels": {},
        "rankings": [],
        "candidates": [],
        "analysis": None,
        "diagnostics": {},
        "sources": [],
        "error": None,
        "disclaimer": "仅供A股条件化研究，不构成投资建议，不连接券商，不自动下单，也不保证收益。",
    }
    try:
        if base["phase"]["code"] == "PREOPEN":
            prior_close = latest_successful_snapshot(previous, history_dir)
            if prior_close:
                result = previous_close_snapshot(prior_close, now)
                write_json(output, result)
                return result

        client = MarketClient()
        with ThreadPoolExecutor(max_workers=2) as pool:
            market_future = pool.submit(client.market_snapshot)
            index_future = pool.submit(client.index_quotes)
            rows, source = market_future.result()
            try:
                indices = index_future.result()
            except Exception:
                indices = []
        market_times = sorted(row["market_time"] for row in rows if row.get("market_time"))
        if not market_times:
            raise RuntimeError("全市场行情缺少时间戳")
        trade_date = market_times[-1][:10]
        if trade_date != now.date().isoformat():
            raise RuntimeError(f"行情日期 {trade_date} 与当前日期 {now.date().isoformat()} 不一致")
        if len(rows) < cfg["universe"]["minimum_market_rows"]:
            raise RuntimeError(f"主板有效行情仅 {len(rows)} 行")

        summary, sector_bundle = market_summary(rows, previous, cfg)
        pool_rows, funnel = prefilter(rows, sector_bundle["items"], cfg)
        target_rows = pool_rows[: cfg["universe"]["detail_limit"]]
        details, detail_errors = client.details([row["code"] for row in target_rows], now.date())
        first_pass = []
        for quote in target_rows:
            if quote["code"] not in details:
                continue
            first_pass.append(
                score_candidate(
                    quote,
                    details[quote["code"]],
                    sector_bundle["items"].get(quote.get("industry")),
                    summary,
                    cfg,
                    trade_date,
                    now=now,
                )
            )
        first_pass.sort(key=lambda item: (-item["score"], -item["amount"], item["code"]))

        announcement_map, announcement_errors = {}, {}
        announcement_sources, announcement_attempt_errors = {}, {}
        announcement_targets = [item["code"] for item in first_pass[:12]]
        with ThreadPoolExecutor(max_workers=3) as announce_pool:
            futures = {
                announce_pool.submit(client.announcement_result, code, now.date(), cfg["scoring"]["announcement_lookback_days"]): code
                for code in announcement_targets
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    result = future.result()
                    announcement_map[code] = result["items"]
                    announcement_sources[code] = result["source"]
                    if result.get("errors"):
                        announcement_attempt_errors[code] = result["errors"]
                except Exception as exc:
                    announcement_errors[code] = safe_error(exc)

        quote_by_code = {row["code"]: row for row in target_rows}
        final_scores = []
        for item in first_pass:
            code = item["code"]
            quote = quote_by_code[code]
            final_scores.append(
                score_candidate(
                    quote,
                    details[code],
                    sector_bundle["items"].get(quote.get("industry")),
                    summary,
                    cfg,
                    trade_date,
                    announcements=announcement_map.get(code),
                    announcement_error=announcement_errors.get(code),
                    announcement_checked=code in announcement_targets,
                    now=now,
                )
            )
        final_scores.sort(key=lambda item: (-item["score"], -item["amount"], item["code"]))
        for rank, item in enumerate(final_scores, 1):
            item["rank"] = rank
            item["in_candidate_pool"] = item["score"] >= cfg["levels"]["weak"]
        rankings = deepcopy(final_scores[: cfg["universe"].get("ranking_limit", 5)])
        candidates = deepcopy(
            [item for item in final_scores if item["in_candidate_pool"]][: cfg["universe"]["candidate_limit"]]
        )

        ordinary_qualified = sum(item["channels"]["ordinary"]["qualified"] for item in candidates)
        hot_qualified = sum(item["channels"]["hot"]["qualified"] for item in candidates)
        base.update(
            {
                "status": "SUCCESS",
                "trade_date": trade_date,
                "data_context": {
                    "code": "CURRENT_SESSION",
                    "label": "当日盘面",
                    "trade_date": trade_date,
                    "message": "行情日期已通过当日完整性检查。",
                },
                "market": summary,
                "indices": indices,
                "sectors": sector_bundle["public"],
                "channels": {
                    "ordinary": {
                        "name": "普通隔夜",
                        "open": bool(base["phase"]["ordinary_open"] and summary["regime"] != "RISK_OFF"),
                        "qualified_count": ordinary_qualified,
                        "message": "市场闸门关闭" if summary["regime"] == "RISK_OFF" else "仅在尾盘窗口执行" if not base["phase"]["ordinary_open"] else "等待个股条件共振",
                    },
                    "hot": {
                        "name": "热点波段",
                        "open": bool(base["phase"]["hot_open"]),
                        "qualified_count": hot_qualified,
                        "message": "当前不在盘中执行窗口" if not base["phase"]["hot_open"] else "仅评估连续强板块中的核心",
                    },
                },
                "rankings": rankings,
                "candidates": candidates,
                "diagnostics": {
                    "market_rows": len(rows),
                    "prefilter": funnel,
                    "details_requested": len(target_rows),
                    "details_succeeded": len(details),
                    "detail_errors": detail_errors[:10],
                    "announcement_targets": len(announcement_targets),
                    "announcement_successes": len(announcement_map),
                    "announcement_failures": len(announcement_errors),
                    "announcement_status": (
                        "FAILED"
                        if announcement_targets and not announcement_map
                        else "PARTIAL"
                        if announcement_errors
                        else "SUCCESS"
                    ),
                    "announcement_sources": announcement_sources,
                    "announcement_attempt_errors": announcement_attempt_errors,
                    "announcement_errors": announcement_errors,
                    "shortlist_before_limit": sum(item["score"] >= cfg["levels"]["weak"] for item in final_scores),
                    "ranking_count": len(rankings),
                },
                "sources": [
                    source,
                    {"source": "腾讯财经前复权日K与分钟线", "source_url": "https://web.ifzq.gtimg.cn/"},
                    {"source": "巨潮资讯正式公告", "source_url": "https://www.cninfo.com.cn/"},
                    {"source": "深圳证券交易所公告备份", "source_url": "https://www.szse.cn/"},
                    {"source": "东方财富公告备份", "source_url": "https://np-anotice-stock.eastmoney.com/"},
                ],
            }
        )
        base["analysis"] = build_analysis(base)
        write_json(output, base)
        if should_write_close_history(now):
            write_json(history_dir / f"{trade_date}-close.json", base)
        return base
    except Exception as exc:
        base["status"] = "FAILED"
        base["error"] = safe_error(exc)
        base["channels"] = {
            "ordinary": {"name": "普通隔夜", "open": False, "qualified_count": 0, "message": "数据失败，停止筛选"},
            "hot": {"name": "热点波段", "open": False, "qualified_count": 0, "message": "数据失败，停止筛选"},
        }
        if previous and previous.get("status") == "SUCCESS":
            base["last_successful"] = {"generated_at": previous.get("generated_at"), "trade_date": previous.get("trade_date")}
        base["analysis"] = build_analysis(base)
        write_json(output, base)
        return base
