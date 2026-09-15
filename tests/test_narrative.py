import unittest

from dashboard.narrative import build_analysis


class NarrativeTests(unittest.TestCase):
    def test_failed_snapshot_does_not_generate_market_view(self):
        payload = {"status": "FAILED", "error": "行情日期过期"}
        result = build_analysis(payload)
        self.assertEqual(result["sections"], [])
        self.assertIn("停止筛选", result["summary"])
        self.assertIn("行情日期过期", result["summary"])

    def test_successful_snapshot_uses_only_visible_candidates(self):
        payload = {
            "status": "SUCCESS",
            "phase": {"label": "午后盘"},
            "market": {
                "regime": "NEUTRAL",
                "breadth": 0.6,
                "advancers": 600,
                "decliners": 400,
                "median_pct": 0.25,
                "limit_up_count": 12,
                "limit_down_count": 2,
                "market_amount": 800_000_000_000,
                "continuity_available": True,
            },
            "sectors": [{"sector": "电力", "strong": True, "confirmed": True, "relative_strength": 1.6, "breadth": 0.72}],
            "channels": {
                "ordinary": {"open": False, "qualified_count": 0, "message": "仅在尾盘窗口执行"},
                "hot": {"open": True, "qualified_count": 1, "message": "仅评估连续强板块中的核心"},
            },
            "candidates": [{
                "name": "示例股份",
                "code": "600001",
                "score": 82,
                "modules": [{"key": "A", "state": "通过"}, {"key": "D", "state": "数据不足"}],
                "plan": {"feasible": True},
            }],
            "diagnostics": {"detail_errors": []},
        }
        result = build_analysis(payload)
        text = "".join(item["text"] for item in result["sections"])
        self.assertIn("电力", text)
        self.assertIn("示例股份（600001）82分", text)
        self.assertIn("仍需确认模块：D", text)
        self.assertNotIn("推荐", text)


if __name__ == "__main__":
    unittest.main()
