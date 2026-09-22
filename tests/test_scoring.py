import copy
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.scoring import build_leader_board, market_summary, score_candidate, technical_features


TZ = ZoneInfo("Asia/Shanghai")


def config():
    import json
    from pathlib import Path

    return json.loads((Path(__file__).parents[1] / "config/scoring_candidate.json").read_text())


def quote():
    return {
        "code": "600001",
        "name": "示例股份",
        "industry": "测试行业",
        "price": 11.0,
        "change_pct": 4.0,
        "amount": 800_000_000,
        "turnover_rate": 5.0,
        "high": 11.1,
        "low": 10.3,
        "open": 10.5,
        "previous_close": 10.58,
        "market_time": "2026-09-15T14:30:00+08:00",
    }


def detail():
    rows = []
    for index in range(30):
        close = 9.2 + index * 0.055
        rows.append({"date": f"2026-08-{index + 1:02d}" if index < 15 else f"2026-09-{index - 14:02d}", "open": close - 0.08, "close": close, "high": close + 0.12, "low": close - 0.14, "volume": 1000 + index * 20})
    rows[-1].update({"date": "2026-09-15", "open": 10.5, "close": 11.0, "high": 11.1, "low": 10.3, "volume": 2200})
    minutes = []
    for index in range(60):
        value = 10.55 + index * 0.0075
        minutes.append({"time": f"2026-09-15T{13 + (30 + index) // 60:02d}:{(30 + index) % 60:02d}+08:00", "open": value - 0.01, "close": value, "high": value + 0.02, "low": value - 0.02, "volume": 100 + index})
    return {"daily": rows, "minute": minutes}


def sector(previous=True):
    return {"sector": "测试行业", "members": 30, "median_pct": 2.1, "breadth": 0.76, "amount": 20_000_000_000, "limit_up_count": 3, "relative_strength": 2.4, "amount_percentile": 0.9, "strong": True, "previous_strong": previous, "confirmed": previous}


class ScoringTests(unittest.TestCase):
    def test_module_weights_sum_to_100(self):
        self.assertEqual(sum(config()["weights"].values()), 100)

    def test_leader_board_is_independent_and_returns_leader_and_capacity_core(self):
        rows = [
            {"code": "600001", "name": "涨幅龙头", "industry": "测试行业", "change_pct": 8.0, "amount": 300_000_000, "turnover_rate": 6.0},
            {"code": "600002", "name": "容量核心", "industry": "测试行业", "change_pct": 3.0, "amount": 2_000_000_000, "turnover_rate": 4.0},
            {"code": "600003", "name": "样本三", "industry": "测试行业", "change_pct": 2.5, "amount": 500_000_000, "turnover_rate": 5.0},
            {"code": "600004", "name": "样本四", "industry": "测试行业", "change_pct": 2.0, "amount": 400_000_000, "turnover_rate": 5.0},
            {"code": "600005", "name": "样本五", "industry": "测试行业", "change_pct": 1.5, "amount": 350_000_000, "turnover_rate": 5.0},
        ]
        sectors = {
            "测试行业": {
                "strong": True,
                "confirmed": True,
                "relative_strength": 2.0,
                "breadth": 0.8,
                "median_pct": 2.5,
                "limit_up_count": 1,
                "amount": 3_550_000_000,
            }
        }
        result = build_leader_board(rows, sectors, config())
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["sectors"][0]["sector"], "测试行业")
        self.assertEqual(result["sectors"][0]["leaders"][0]["role"], "龙头")
        self.assertEqual(result["sectors"][0]["leaders"][0]["code"], "600001")
        self.assertEqual(result["sectors"][0]["leaders"][1]["role"], "容量核心")
        self.assertEqual(result["sectors"][0]["leaders"][1]["code"], "600002")

    def test_leader_board_requires_confirmed_sector_without_affecting_trade_score(self):
        rows = [
            {"code": f"60000{index}", "name": "样本", "industry": "测试行业", "change_pct": 2.0, "amount": 300_000_000, "turnover_rate": 5.0}
            for index in range(1, 6)
        ]
        sectors = {"测试行业": {"strong": True, "confirmed": False, "relative_strength": 1.0, "breadth": 0.8, "median_pct": 2.0, "amount": 1_500_000_000}}
        result = build_leader_board(rows, sectors, config())
        self.assertEqual(result["status"], "NO_CONFIRMED_SECTOR")
        self.assertEqual(result["sectors"], [])

    def test_missing_previous_sector_snapshot_cannot_pass_d(self):
        result = score_candidate(quote(), detail(), sector(False), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        module_d = next(item for item in result["modules"] if item["key"] == "D")
        self.assertEqual(module_d["state"], "数据不足")
        self.assertLessEqual(module_d["score"], 10)
        self.assertFalse(result["channels"]["hot"]["qualified"])

    def test_risk_off_closes_ordinary_even_with_strong_stock(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_OFF"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertFalse(result["channels"]["ordinary"]["qualified"])

    def test_score_is_exact_sum_of_visible_modules(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertEqual(result["score"], sum(item["score"] for item in result["modules"]))
        self.assertEqual([item["key"] for item in result["modules"]], ["A", "B", "C", "D", "E", "G"])

    def test_tail_momentum_is_not_scored_before_1420(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 19, tzinfo=TZ))
        module_b = next(item for item in result["modules"] if item["key"] == "B")
        self.assertEqual(module_b["score"], 0)
        self.assertEqual(module_b["state"], "数据不足")
        self.assertIn("14:20前", module_b["evidence"][0])
        self.assertTrue(result["channels"]["hot"]["qualified"])
        self.assertEqual(result["channels"]["hot"]["required_modules"], ["A", "D", "G"])

    def test_data_confidence_is_separate_from_trade_score(self):
        checked = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        unchecked = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], announcement_checked=False, now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertEqual(checked["score"] - unchecked["score"], 5)
        self.assertGreater(checked["data_confidence"]["score"], unchecked["data_confidence"]["score"])
        self.assertEqual(checked["data_confidence"]["max"], 100)

    def test_source_fallback_reduces_data_confidence_without_changing_trade_score(self):
        baseline = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        degraded_quote = quote()
        degraded_quote.update({
            "data_source": "东方财富沪深A股行情",
            "received_at": "2026-09-15T14:30:30+08:00",
            "timestamp_source": "fallback_quote",
            "source_fallback": True,
            "source_fallback_reason": "TdxAiData主源请求失败",
        })
        degraded_detail = detail()
        degraded_detail["provenance"] = {
            "daily_source": "腾讯财经前复权日K",
            "minute_source": "腾讯财经分钟K",
            "received_at": "2026-09-15T14:30:30+08:00",
            "minute_provider_time": "2026-09-15T14:20:00+08:00",
            "minute_latency_seconds": 630,
            "fallback": True,
            "tdx_fallback": True,
            "fallback_reason": "TdxAiData详细数据缺失，使用腾讯财经回退",
        }
        degraded = score_candidate(degraded_quote, degraded_detail, sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertEqual(degraded["score"], baseline["score"])
        self.assertLess(degraded["data_confidence"]["source_quality_score"], 100)
        self.assertLess(degraded["data_confidence"]["score"], baseline["data_confidence"]["score"])
        self.assertTrue(degraded["data_confidence"]["source_quality"]["fallback"])

    def test_tail_momentum_can_be_scored_at_1420(self):
        result = score_candidate(quote(), detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 20, tzinfo=TZ))
        module_b = next(item for item in result["modules"] if item["key"] == "B")
        self.assertGreater(module_b["score"], 0)
        self.assertNotIn("14:20前", module_b["evidence"][0])

    def test_all_announcement_sources_failed_scores_zero_without_blocking_candidate(self):
        result = score_candidate(
            quote(),
            detail(),
            sector(True),
            {"regime": "RISK_ON"},
            config(),
            "2026-09-15",
            announcement_error="all sources failed",
            now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ),
        )
        module_e = next(item for item in result["modules"] if item["key"] == "E")
        self.assertEqual(module_e["score"], 0)
        self.assertEqual(module_e["state"], "数据不足")
        self.assertNotEqual(result["level"], "排除")

    def test_unchecked_announcement_is_not_treated_as_verified_empty(self):
        result = score_candidate(
            quote(),
            detail(),
            sector(True),
            {"regime": "RISK_ON"},
            config(),
            "2026-09-15",
            announcement_checked=False,
            now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ),
        )
        module_e = next(item for item in result["modules"] if item["key"] == "E")
        self.assertEqual(module_e["score"], 0)
        self.assertIn("未进入公告复核范围", module_e["evidence"][0])

    def test_failed_modules_never_exceed_40_percent(self):
        risky = quote()
        risky["turnover_rate"] = 30.0
        result = score_candidate(risky, detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        for module in result["modules"]:
            if module["state"] == "不通过":
                self.assertLessEqual(module["score"], int(module["max"] * 0.4))

    def test_impossible_trigger_range_blocks_both_channels(self):
        blocked = detail()
        blocked["daily"][-2]["high"] = 20.0
        result = score_candidate(quote(), blocked, sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [{"title": "关于股份回购的公告"}], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertFalse(result["plan"]["feasible"])
        self.assertFalse(result["channels"]["ordinary"]["qualified"])
        self.assertFalse(result["channels"]["hot"]["qualified"])

    def test_market_sector_needs_previous_confirmation(self):
        rows = []
        for index in range(20):
            rows.append({"code": f"600{index:03d}", "name": "样本", "change_pct": 2 if index < 16 else -1, "amount": 300_000_000, "industry": "测试行业", "market_time": "2026-09-15T10:30:00+08:00"})
        for index in range(20, 40):
            rows.append({"code": f"600{index:03d}", "name": "对照", "change_pct": -2 if index < 36 else 1, "amount": 200_000_000, "industry": "弱势行业", "market_time": "2026-09-15T10:30:00+08:00"})
        summary, current = market_summary(rows, None, config())
        self.assertFalse(current["items"]["测试行业"]["confirmed"])
        previous = {"generated_at": "2026-09-15T10:25:00+08:00", "sectors": current["public"]}
        _, confirmed = market_summary(rows, previous, config())
        self.assertTrue(confirmed["items"]["测试行业"]["confirmed"])

    def test_cross_day_sector_continuity_is_required_for_hot_channel(self):
        rows = [
            {"code": f"600{index:03d}", "name": "样本", "change_pct": 2.5, "amount": 300_000_000, "industry": "测试行业", "market_time": "2026-09-21T10:30:00+08:00"}
            for index in range(10)
        ]
        rows.extend(
            {"code": f"601{index:03d}", "name": "对照", "change_pct": -2.0, "amount": 200_000_000, "industry": "弱势行业", "market_time": "2026-09-21T10:30:00+08:00"}
            for index in range(10)
        )
        previous = {
            "trade_date": "2026-09-18",
            "generated_at": "2026-09-18T15:05:00+08:00",
            "diagnostics": {"sector_continuity": [{"sector": "测试行业", "strong": True, "snapshot_streak": 4, "strong_trade_days": 1, "last_trade_date": "2026-09-18"}]},
        }
        _, current = market_summary(rows, previous, config())
        item = current["items"]["测试行业"]
        self.assertTrue(item["confirmed_swing"])
        self.assertFalse(item["confirmed_intraday"])
        self.assertEqual(item["snapshot_streak"], 1)
        self.assertEqual(item["strong_trade_days"], 2)

    def test_current_execution_requires_price_trigger(self):
        waiting_quote = quote()
        waiting_quote.update({"price": 10.8, "high": 10.9, "change_pct": 2.1})
        result = score_candidate(waiting_quote, detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertTrue(result["channels"]["hot"]["qualified"])
        self.assertFalse(result["channels"]["hot"]["actionable_now"])
        self.assertEqual(result["channels"]["hot"]["action_state"], "WAIT_TRIGGER")

    def test_stale_quote_blocks_execution_without_removing_qualification(self):
        stale_quote = quote()
        stale_quote["market_time"] = "2026-09-15T14:00:00+08:00"
        result = score_candidate(stale_quote, detail(), sector(True), {"regime": "RISK_ON"}, config(), "2026-09-15", [], now=datetime(2026, 9, 15, 14, 30, tzinfo=TZ))
        self.assertTrue(result["channels"]["hot"]["qualified"])
        self.assertFalse(result["channels"]["hot"]["actionable_now"])
        self.assertEqual(result["channels"]["hot"]["action_state"], "DATA_STALE")

    def test_intraday_volume_prefers_same_time_history(self):
        sample = detail()
        current = []
        previous = []
        for index in range(30):
            minute = 30 + index
            current.append({"time": f"2026-09-15T09:{minute:02d}:00+08:00", "open": 10, "close": 10, "high": 10.1, "low": 9.9, "volume": 200})
            previous.append({"time": f"2026-09-14T09:{minute:02d}:00+08:00", "open": 10, "close": 10, "high": 10.1, "low": 9.9, "volume": 100})
        sample["minute"] = previous + current
        features = technical_features(quote(), sample, "2026-09-15")
        self.assertEqual(features["volume_ratio_method"], "same_time_minutes")
        self.assertAlmostEqual(features["volume_ratio"], 2.0)


if __name__ == "__main__":
    unittest.main()
