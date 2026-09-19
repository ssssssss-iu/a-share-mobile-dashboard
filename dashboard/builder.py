from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .data import MarketClient, load_json
from .intraday_history import record_intraday_snapshot
from .narrative import build_analysis
from .research_history import record_research_snapshot
from .schedule import should_write_close_history
from .scoring import market_summary, phase_at, prefilter, score_candidate, source_quality
from .tdxaidata_provider import TdxAiDataSource, source_record


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


def attach_tdxaidata_status(snapshot: dict, status: dict) -> None:
    snapshot.setdefault("diagnostics", {})["tdxaidata"] = status
    sources = [
        item for item in snapshot.get("sources") or []
        if not str(item.get("source") or "").startswith("通达信TdxAiData")
    ]
    sources.append(source_record(status))
    snapshot["sources"] = sources


def source_trace(universe_rows: list[dict], rows: list[dict], details: dict, tdx_status: dict | None = None) -> dict:
    """Expose field-level source and timestamp coverage without exposing credentials."""
    universe_sources = Counter(str(row.get("data_source") or "未知") for row in universe_rows)
    quote_sources = Counter(str(row.get("data_source") or "未知") for row in rows)
    quote_timestamps = Counter(str(row.get("timestamp_source") or "missing") for row in rows)
    quote_lags = [row.get("source_latency_seconds") for row in rows if isinstance(row.get("source_latency_seconds"), (int, float))]
    detail_meta = [value.get("provenance") or {} for value in details.values() if isinstance(value, dict)]
    detail_sources = Counter(
        str(meta.get("minute_source") or meta.get("daily_source") or "未知") for meta in detail_meta
    )
    detail_timestamps = Counter(str(meta.get("timestamp_source") or "missing") for meta in detail_meta)
    detail_lags = [
        value
        for meta in detail_meta
        for value in (meta.get("minute_latency_seconds"), meta.get("daily_latency_seconds"))
        if isinstance(value, (int, float))
    ]
    return {
        "universe_quote_sources": dict(universe_sources),
        "quote_sources": dict(quote_sources),
        "quote_timestamp_sources": dict(quote_timestamps),
        "quote_received_at": max((row.get("received_at") for row in rows if row.get("received_at")), default=None),
        "quote_provider_time_min": min((row.get("market_time") for row in rows if row.get("market_time")), default=None),
        "quote_provider_time_max": max((row.get("market_time") for row in rows if row.get("market_time")), default=None),
        "quote_latency_seconds_max": max(quote_lags) if quote_lags else None,
        "detail_sources": dict(detail_sources),
        "detail_timestamp_sources": dict(detail_timestamps),
        "detail_latency_seconds_max": max(detail_lags) if detail_lags else None,
        "tdxaidata": {
            "status": (tdx_status or {}).get("status"),
            "received_at": (tdx_status or {}).get("received_at"),
            "provider_time_min": (tdx_status or {}).get("provider_time_min"),
            "provider_time_max": (tdx_status or {}).get("provider_time_max"),
        },
    }


def merge_tdx_status(current: dict | None, incoming: dict | None) -> dict | None:
    """Merge per-batch Tdx diagnostics when the detail pool is expanded."""
    if not current:
        return deepcopy(incoming) if incoming else None
    if not incoming:
        return current
    merged = dict(current)
    for key in ("primary_quote_count", "primary_detail_count", "fallback_quote_count", "fallback_detail_count", "requested_count"):
        merged[key] = int(current.get(key) or 0) + int(incoming.get(key) or 0)
    merged["affects_scoring"] = bool(current.get("affects_scoring") or incoming.get("affects_scoring"))
    merged["status"] = "CONNECTED" if current.get("status") == incoming.get("status") == "CONNECTED" else "PRIMARY_WITH_FALLBACK"
    merged["received_at"] = max(
        value for value in (current.get("received_at"), incoming.get("received_at")) if value
    ) if current.get("received_at") or incoming.get("received_at") else None
    provider_times = [
        value
        for value in (current.get("provider_time_min"), current.get("provider_time_max"), incoming.get("provider_time_min"), incoming.get("provider_time_max"))
        if value
    ]
    merged["provider_time_min"] = min(provider_times) if provider_times else None
    merged["provider_time_max"] = max(provider_times) if provider_times else None
    counts = {}
    for item in (current.get("timestamp_source_counts") or {}, incoming.get("timestamp_source_counts") or {}):
        for key, value in item.items():
            counts[key] = counts.get(key, 0) + int(value or 0)
    merged["timestamp_source_counts"] = counts
    merged["provenance"] = list(current.get("provenance") or []) + list(incoming.get("provenance") or [])
    if incoming.get("message"):
        merged["message"] = incoming["message"]
    return merged


def latest_successful_snapshot(previous: dict | None, history_dir: Path) -> dict | None:
    if previous and previous.get("status") == "SUCCESS":
        return previous
    for path in sorted(history_dir.glob("*.json"), reverse=True):
        snapshot = load_json(path)
        if snapshot and snapshot.get("status") == "SUCCESS":
            return snapshot
    return None


def previous_close_snapshot(
    snapshot: dict,
    now: datetime,
    context_code: str = "PREVIOUS_CLOSE",
    context_label: str = "上一交易日收盘",
    context_message: str = "9:25竞价节点前展示最近一次成功收盘快照，不执行新筛选。",
) -> dict:
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
        "code": context_code,
        "label": context_label,
        "trade_date": trade_date,
        "source_generated_at": source_generated_at,
        "message": context_message,
    }
    for key in ("ordinary", "hot"):
        channel = result.get("channels", {}).get(key)
        if channel:
            channel["open"] = False
            channel["message"] = "当前仅展示最近交易日收盘，不执行新筛选"
    for collection in ("rankings", "score_pool", "candidates"):
        for candidate in result.get(collection) or []:
            for channel in (candidate.get("channels") or {}).values():
                channel["actionable_now"] = False
    layers = result.setdefault("layers", {})
    layers["ordinary_actionable_count"] = 0
    layers["hot_actionable_count"] = 0
    result.setdefault("diagnostics", {})["previous_close_reused"] = True
    result["diagnostics"]["source_generated_at"] = source_generated_at
    result["analysis"] = build_analysis(result)
    return result


def build(
    output: Path | None = None,
    history_dir: Path | None = None,
    now: datetime | None = None,
    intraday_dir: Path | None = None,
    research_dir: Path | None = None,
    trigger: str | None = None,
    scheduled_time: str | None = None,
) -> dict:
    output = output or ROOT / "site/latest.json"
    history_dir = history_dir or ROOT / "site/history"
    intraday_dir = intraday_dir or output.parent / "intraday"
    research_dir = research_dir or output.parent / "research"
    cfg = load_json(ROOT / "config/scoring_candidate.json")
    if not cfg:
        raise RuntimeError("评分配置无法读取")
    tdx_source = TdxAiDataSource(cfg.get("data_sources", {}).get("tdxaidata", {}))
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
            "parameter_set_id": cfg.get("parameter_set_id"),
            "weights": cfg["weights"],
            "note": "模块权重来自生效策略；通道资格已按必过模块独立判定，精确数值阈值继续作为前向验证参数。",
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
        if now.weekday() >= 5:
            prior_close = latest_successful_snapshot(previous, history_dir)
            if prior_close:
                result = previous_close_snapshot(
                    prior_close,
                    now,
                    context_code="NON_TRADING_DAY",
                    context_label="非交易日·上一交易日收盘",
                    context_message="周末展示最近一次成功收盘快照，全部执行通道保持关闭。",
                )
                attach_tdxaidata_status(result, tdx_source.smoke())
                write_json(output, result)
                return result
        if base["phase"]["code"] == "PREOPEN":
            prior_close = latest_successful_snapshot(previous, history_dir)
            if prior_close:
                result = previous_close_snapshot(prior_close, now)
                attach_tdxaidata_status(result, tdx_source.smoke())
                write_json(output, result)
                return result

        client = MarketClient(
            minimum_quote_coverage=float(cfg["universe"].get("minimum_quote_coverage", 0.90))
        )
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
        expansion_cfg = cfg.get("detail_expansion") or {}
        initial_limit = int(cfg["universe"]["detail_limit"])
        max_limit = min(int(cfg["universe"].get("prefilter_limit", initial_limit)), len(pool_rows))
        requested_limits = expansion_cfg.get("limits") or [initial_limit, max_limit]
        detail_limits = []
        for value in [initial_limit, *requested_limits]:
            value = min(max(1, int(value)), max_limit)
            if value not in detail_limits:
                detail_limits.append(value)
        detail_limits.sort()
        if not expansion_cfg.get("enabled", True):
            detail_limits = [initial_limit]

        target_by_code, details, detail_errors = {}, {}, []
        tdx_status = None
        detail_steps, expansion_reasons = [], []

        def fetch_detail_batch(batch_rows):
            batch_codes = [row["code"] for row in batch_rows]
            if tdx_source.mode == "primary":
                fallback_quotes = {row["code"]: row for row in batch_rows}
                primary_quotes, primary_details, batch_status = tdx_source.primary_data(
                    batch_codes,
                    trade_date,
                    base["phase"]["code"],
                    fallback_quotes,
                    {},
                    now,
                )
                batch_rows = [primary_quotes.get(row["code"], row) for row in batch_rows]
                batch_details = primary_details
                batch_errors = []
                if batch_status.get("fallback_detail_count"):
                    batch_details, batch_errors = client.details(batch_codes, now.date())
                    primary_detail_codes = set(primary_details)
                    for code, detail in batch_details.items():
                        if code in primary_detail_codes:
                            continue
                        provenance = detail.setdefault("provenance", {})
                        provenance["tdx_fallback"] = True
                        provenance["fallback"] = True
                        provenance["fallback_reason"] = "TdxAiData详细数据缺失，使用腾讯财经回退"
                    batch_details.update(primary_details)
                return batch_rows, batch_details, batch_errors, batch_status
            batch_details, batch_errors = client.details(batch_codes, now.date())
            return batch_rows, batch_details, batch_errors, None

        def score_first_pass(quote_rows):
            scored = []
            for quote in quote_rows:
                if quote["code"] not in details:
                    continue
                scored.append(
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
            scored.sort(key=lambda item: (-item["score"], -item["amount"], item["code"]))
            return scored

        first_pass = []
        for limit in detail_limits:
            batch_rows = [row for row in pool_rows[:limit] if row["code"] not in target_by_code]
            if batch_rows:
                fetched_rows, batch_details, batch_errors, batch_status = fetch_detail_batch(batch_rows)
                target_by_code.update({row["code"]: row for row in fetched_rows})
                details.update(batch_details)
                detail_errors.extend(batch_errors)
                tdx_status = merge_tdx_status(tdx_status, batch_status)
            target_rows = [target_by_code[row["code"]] for row in pool_rows[:limit] if row["code"] in target_by_code]
            detail_steps.append(len(target_rows))
            first_pass = score_first_pass(target_rows)
            if limit == detail_limits[-1]:
                break

            fallback_ratio = 0.0
            if tdx_status:
                requested_count = max(1, int(tdx_status.get("requested_count") or len(target_rows)))
                fallback_ratio = max(
                    int(tdx_status.get("fallback_quote_count") or 0),
                    int(tdx_status.get("fallback_detail_count") or 0),
                ) / requested_count
            quality_scores = [
                source_quality(target_by_code[item["code"]], details[item["code"]], cfg)["score"]
                for item in first_pass
                if item["code"] in target_by_code and item["code"] in details
            ]
            average_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None
            source_can_expand = not tdx_status or tdx_status.get("status") == "CONNECTED" or int(tdx_status.get("primary_quote_count") or 0) > 0
            reasons = []
            if len(first_pass) < int(expansion_cfg.get("minimum_complete_candidates", 5)):
                reasons.append("完整评分候选不足")
            if source_can_expand and fallback_ratio > float(expansion_cfg.get("maximum_fallback_ratio", 0.20)):
                reasons.append("主源回退比例过高")
            if source_can_expand and average_quality is not None and average_quality < float(expansion_cfg.get("minimum_source_quality_score", 70)):
                reasons.append("来源质量低于阈值")
            if not reasons:
                break
            expansion_reasons.append({"from": limit, "to": detail_limits[detail_limits.index(limit) + 1], "reasons": reasons})

        target_rows = [target_by_code[row["code"]] for row in pool_rows if row["code"] in target_by_code]

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
                    announcement_source=announcement_sources.get(code),
                    now=now,
                )
            )
        final_scores.sort(key=lambda item: (-item["score"], -item["amount"], item["code"]))
        for rank, item in enumerate(final_scores, 1):
            item["rank"] = rank
            item["in_score_pool"] = item["score"] >= cfg["levels"]["weak"]
            item["in_candidate_pool"] = item["in_score_pool"]  # compatibility with older snapshots
        rankings = deepcopy(final_scores[: cfg["universe"].get("ranking_limit", 5)])
        score_pool = deepcopy(
            [item for item in final_scores if item["in_score_pool"]][: cfg["universe"]["candidate_limit"]]
        )
        candidates = deepcopy(score_pool)  # compatibility for older page clients
        if tdx_status is None:
            tdx_status = tdx_source.validate(
                [item["code"] for item in final_scores],
                trade_date,
                quote_by_code,
                details,
                base["phase"]["code"],
            )

        ordinary_qualified = sum(item["channels"]["ordinary"]["qualified"] for item in final_scores)
        hot_qualified = sum(item["channels"]["hot"]["qualified"] for item in final_scores)
        ordinary_actionable = sum(item["channels"]["ordinary"]["actionable_now"] for item in final_scores)
        hot_actionable = sum(item["channels"]["hot"]["actionable_now"] for item in final_scores)
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
                "score_pool": score_pool,
                "candidates": candidates,
                "layers": {
                    "ranked_count": len(rankings),
                    "score_pool_count": sum(item["in_score_pool"] for item in final_scores),
                    "ordinary_qualified_count": ordinary_qualified,
                    "hot_qualified_count": hot_qualified,
                    "ordinary_actionable_count": ordinary_actionable,
                    "hot_actionable_count": hot_actionable,
                    "definitions": {
                        "ranking": "完整评分中的总分前五",
                        "score_pool": "总分达到60分的观察池",
                        "qualified": "对应通道必过模块、风险和买点结构均通过",
                        "actionable": "通道合格且当前处于执行窗口",
                    },
                },
                "diagnostics": {
                    "market_rows": len(rows),
                    "prefilter": funnel,
                    "details_requested": len(target_rows),
                    "details_succeeded": len(details),
                    "detail_errors": detail_errors[:10],
                    "detail_expansion": {
                        "enabled": bool(expansion_cfg.get("enabled", True)),
                        "requested_limits": detail_limits,
                        "steps": detail_steps,
                        "expanded": len(detail_steps) > 1,
                        "reasons": expansion_reasons,
                        "initial_limit": initial_limit,
                        "final_limit": len(target_rows),
                    },
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
                    "fully_scored_count": len(final_scores),
                    "tdxaidata": tdx_status,
                    "source_trace": source_trace(rows, target_rows, details, tdx_status),
                },
                "sources": [
                    source,
                    source_record(tdx_status),
                    {"source": "腾讯财经前复权日K与分钟线", "source_url": "https://web.ifzq.gtimg.cn/"},
                    {"source": "巨潮资讯正式公告", "source_url": "https://www.cninfo.com.cn/"},
                    {"source": "深圳证券交易所公告备份", "source_url": "https://www.szse.cn/"},
                    {"source": "东方财富公告备份", "source_url": "https://np-anotice-stock.eastmoney.com/"},
                ],
            }
        )
        base["analysis"] = build_analysis(base)
        try:
            base["diagnostics"]["intraday_history_saved"] = record_intraday_snapshot(
                base,
                intraday_dir,
                now,
                trigger=trigger or os.getenv("DASHBOARD_TRIGGER", "manual"),
                scheduled_time=scheduled_time or os.getenv("DASHBOARD_SCHEDULED_TIME"),
            )
        except Exception as history_exc:
            base["diagnostics"]["intraday_history_saved"] = False
            base["diagnostics"]["intraday_history_error"] = safe_error(history_exc)
        try:
            research_result = record_research_snapshot(
                base,
                final_scores,
                rows,
                research_dir,
                now,
                trigger=trigger or os.getenv("DASHBOARD_TRIGGER", "manual"),
                scheduled_time=scheduled_time or os.getenv("DASHBOARD_SCHEDULED_TIME"),
                keep_days=int(cfg.get("research", {}).get("keep_days", 30)),
            )
            base["diagnostics"]["research_snapshot_saved"] = research_result["saved"]
            base["diagnostics"]["research_outcome_files_updated"] = research_result["updated_files"]
            base["diagnostics"]["research_snapshot_path"] = research_result.get("path")
        except Exception as research_exc:
            base["diagnostics"]["research_snapshot_saved"] = False
            base["diagnostics"]["research_history_error"] = safe_error(research_exc)
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
