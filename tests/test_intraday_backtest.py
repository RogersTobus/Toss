import unittest
from datetime import datetime, timedelta, timezone

import server


class IntradayBacktestTests(unittest.TestCase):
    def candles(self, prices):
        start = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
        rows = []
        for index, (open_price, high, low, close) in enumerate(prices):
            rows.append({
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": open_price, "high": high, "low": low, "close": close, "volume": 1000,
            })
        return rows

    def test_same_minute_target_and_stop_assumes_stop_first(self):
        flat = [(100, 100.2, 99.9, 100)] * 6
        both = [(100, 101.5, 99.4, 101)]
        tail = [(101, 101, 100.8, 101)] * 12
        trades = server.simulate_intraday_strategy(self.candles(flat + both + tail), "US", "TEST", "Test", 1)
        self.assertTrue(trades)
        self.assertEqual(trades[0]["exitKind"], "손실선")
        self.assertLessEqual(trades[0]["grossReturnRate"], server.PAPER_STOP_RATE)

    def test_target_then_trailing_combines_both_halves(self):
        flat = [(100, 100.2, 99.9, 100)] * 6
        target = [(100, 101.2, 100.2, 101)]
        trailing = [(101, 102, 101, 101.5), (101.5, 101.6, 101.0, 101.1)]
        tail = [(101.1, 101.2, 101, 101.1)] * 10
        trades = server.simulate_intraday_strategy(self.candles(flat + target + trailing + tail), "US", "TEST", "Test", 1)
        self.assertTrue(trades)
        self.assertEqual(trades[0]["exitKind"], "추적손절")
        self.assertGreater(trades[0]["grossReturnRate"], 0)

    def test_time_ordered_split_keeps_holdout_separate(self):
        trades = [
            {"closedAt": f"2026-07-{index + 1:02d}", "netReturnRate": 0.01 if index % 2 else -0.005,
             "netProfit": 0.01 if index % 2 else -0.005, "invested": 1, "estimatedCost": 0}
            for index in range(10)
        ]
        metrics = server.intraday_backtest_metrics(trades)
        self.assertEqual(metrics["splits"]["train"]["sampleCount"], 6)
        self.assertEqual(metrics["splits"]["validation"]["sampleCount"], 2)
        self.assertEqual(metrics["splits"]["holdout"]["sampleCount"], 2)

