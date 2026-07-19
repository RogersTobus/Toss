import unittest

import server


class PerformanceAnalyticsTests(unittest.TestCase):
    def orders(self):
        return [
            {
                "id": "KR-BUY",
                "market": "KR",
                "symbol": "005930",
                "side": "BUY",
                "quantity": 10,
                "price": 100,
                "createdAt": "2026-07-17T09:10:00+0900",
                "entryScore": 83,
                "strategyIds": ["score-entry-80"],
            },
            {
                "id": "KR-SELL",
                "entryOrderId": "KR-BUY",
                "market": "KR",
                "symbol": "005930",
                "side": "SELL",
                "quantity": 10,
                "price": 101,
                "createdAt": "2026-07-17T09:13:00+0900",
            },
            {
                "id": "US-BUY",
                "market": "US",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "price": 100,
                "createdAt": "2026-07-18T22:40:00+0900",
                "entryScore": 92,
                "strategyIds": ["score-entry-80"],
            },
            {
                "id": "US-SELL",
                "entryOrderId": "US-BUY",
                "market": "US",
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 1,
                "price": 99.5,
                "createdAt": "2026-07-18T22:43:00+0900",
            },
        ]

    def test_cost_adjusted_market_score_strategy_and_time_breakdowns(self):
        analytics = server.trade_performance_analytics_from_orders(self.orders())
        self.assertEqual(analytics["overall"]["sampleCount"], 2)
        self.assertEqual({item["key"] for item in analytics["byMarket"]}, {"KR", "US"})
        self.assertEqual({item["key"] for item in analytics["byScoreBucket"]}, {"80~84점", "90점 이상"})
        self.assertEqual(analytics["byStrategy"][0]["key"], "score-entry-80")
        self.assertLess(analytics["overall"]["totalNetProfit"], 9.5)

    def test_unprotected_position_blocks_new_entries(self):
        buy = self.orders()[0]
        readiness = server.operational_readiness({"enabled": False}, [buy])
        self.assertTrue(readiness["entryBlocked"])
        self.assertEqual(readiness["warnings"][0]["code"], "UNPROTECTED_POSITION")

    def test_working_protective_stop_keeps_entry_ready(self):
        buy = self.orders()[0]
        buy["protectiveStopOrder"] = {"status": "WORKING"}
        readiness = server.operational_readiness({"enabled": False}, [buy])
        self.assertFalse(readiness["entryBlocked"])
        self.assertEqual(readiness["protectedPositionCount"], 1)


if __name__ == "__main__":
    unittest.main()
