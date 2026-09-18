import unittest
from unittest.mock import patch

from dashboard.data import MarketClient


def market_page(page: int, total: int = 1000) -> dict:
    start = (page - 1) * 100
    rows = []
    for offset in range(100):
        code = f"{600000 + start + offset:06d}"
        rows.append({"f12": code, "f14": f"示例{code}", "f2": 10, "f3": 1, "f124": 1789618200})
    return {"total": total, "diff": rows}


class MarketCoverageTests(unittest.TestCase):
    def test_exactly_90_percent_passes_even_when_one_page_failed(self):
        client = MarketClient(workers=1, minimum_quote_coverage=0.90)

        def fetch(page):
            if page == 10:
                raise RuntimeError("page unavailable")
            return market_page(page)

        with patch.object(client, "_market_page", side_effect=fetch):
            rows, source = client.market_snapshot()

        self.assertEqual(len(rows), 900)
        self.assertEqual(source["coverage"], 0.90)
        self.assertEqual(source["minimum_coverage"], 0.90)
        self.assertEqual(source["failed_pages"], 1)

    def test_below_90_percent_fails(self):
        client = MarketClient(workers=1, minimum_quote_coverage=0.90)

        def fetch(page):
            if page in (9, 10):
                raise RuntimeError("page unavailable")
            return market_page(page)

        with patch.object(client, "_market_page", side_effect=fetch):
            with self.assertRaisesRegex(RuntimeError, "低于 90% 门槛"):
                client.market_snapshot()


if __name__ == "__main__":
    unittest.main()
