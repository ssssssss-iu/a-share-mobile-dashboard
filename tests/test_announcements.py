import unittest
from datetime import date
from unittest.mock import Mock, patch

from dashboard.data import MarketClient


class AnnouncementFallbackTests(unittest.TestCase):
    def test_cninfo_403_falls_back_to_szse_for_shenzhen_stock(self):
        client = MarketClient()
        expected = [{"title": "测试公告", "source": "深圳证券交易所正式公告"}]
        with (
            patch.object(client, "_announcements_cninfo", side_effect=RuntimeError("HTTP 403")),
            patch.object(client, "_announcements_szse", return_value=expected) as szse,
            patch.object(client, "_announcements_eastmoney") as eastmoney,
        ):
            result = client.announcement_result("002077", date(2026, 9, 16), 10)
        self.assertEqual(result["items"], expected)
        self.assertEqual(result["source"], "深圳证券交易所正式公告")
        self.assertEqual(len(result["errors"]), 1)
        szse.assert_called_once()
        eastmoney.assert_not_called()

    def test_cninfo_403_falls_back_to_eastmoney_for_shanghai_stock(self):
        client = MarketClient()
        expected = [{"title": "测试公告", "source": "东方财富公告备份"}]
        with (
            patch.object(client, "_announcements_cninfo", side_effect=RuntimeError("HTTP 403")),
            patch.object(client, "_announcements_eastmoney", return_value=expected),
        ):
            result = client.announcement_result("600460", date(2026, 9, 16), 10)
        self.assertEqual(result["items"], expected)
        self.assertEqual(result["source"], "东方财富公告备份")

    def test_empty_szse_fallback_continues_to_eastmoney(self):
        client = MarketClient()
        expected = [{"title": "投资者关系公告", "source": "东方财富公告备份"}]
        with (
            patch.object(client, "_announcements_cninfo", side_effect=RuntimeError("HTTP 403")),
            patch.object(client, "_announcements_szse", return_value=[]),
            patch.object(client, "_announcements_eastmoney", return_value=expected),
        ):
            result = client.announcement_result("002077", date(2026, 9, 16), 10)
        self.assertEqual(result["items"], expected)
        self.assertEqual(result["source"], "东方财富公告备份")

    def test_verified_empty_cninfo_does_not_call_fallback(self):
        client = MarketClient()
        with (
            patch.object(client, "_announcements_cninfo", return_value=[]),
            patch.object(client, "_announcements_szse") as szse,
            patch.object(client, "_announcements_eastmoney") as eastmoney,
        ):
            result = client.announcement_result("002077", date(2026, 9, 16), 10)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["source"], "巨潮资讯正式公告")
        szse.assert_not_called()
        eastmoney.assert_not_called()

    def test_all_sources_failed_raises_combined_error(self):
        client = MarketClient()
        with (
            patch.object(client, "_announcements_cninfo", side_effect=RuntimeError("cninfo failed")),
            patch.object(client, "_announcements_szse", side_effect=RuntimeError("szse failed")),
            patch.object(client, "_announcements_eastmoney", side_effect=RuntimeError("eastmoney failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "巨潮资讯.*深圳证券交易所.*东方财富"):
                client.announcement_result("002077", date(2026, 9, 16), 10)

    def test_cninfo_org_id_uses_official_mapping(self):
        client = MarketClient()
        response = Mock()
        response.json.return_value = {
            "stockList": [
                {"code": f"{index:06d}", "orgId": f"org-{index}"}
                for index in range(1001)
            ]
            + [{"code": "002077", "orgId": "9900001222"}]
        }
        with patch.object(client, "_request", return_value=response):
            self.assertEqual(client._cninfo_org_id("002077"), "9900001222")


if __name__ == "__main__":
    unittest.main()
