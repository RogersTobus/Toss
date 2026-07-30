import unittest
from datetime import datetime, timedelta, timezone

import server


class IntradayBacktestTests(unittest.TestCase):
    def candles(self, prices, start=None):
        start = start or datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
        rows = []
        for index, values in enumerate(prices):
            open_price, high, low, close = values[:4]
            volume = values[4] if len(values) > 4 else 1000
            rows.append({
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": open_price, "high": high, "low": low, "close": close, "volume": volume,
            })
        return rows

    def expand_three_minute_bars(self, bars):
        rows = []
        for open_price, high, low, close, volume in bars:
            first_close = open_price + ((close - open_price) / 3)
            second_close = open_price + ((close - open_price) * 2 / 3)
            rows.extend([
                (open_price, high, low, first_close, volume / 3),
                (first_close, high, low, second_close, volume / 3),
                (second_close, high, low, close, volume / 3),
            ])
        return rows

    def qualifying_prefix(self):
        bars = [
            (100.00, 100.20, 99.80, 100.00, 3000),
            (100.00, 100.15, 99.85, 100.05, 3000),
            (100.05, 100.18, 99.82, 99.98, 3000),
            (99.98, 100.16, 99.84, 100.03, 3000),
            (100.03, 100.17, 99.83, 100.01, 3000),
            (100.01, 100.80, 100.00, 100.60, 4800),
            (100.60, 100.65, 100.15, 100.30, 2400),
            (100.30, 100.85, 100.25, 100.75, 3300),
        ]
        return self.expand_three_minute_bars(bars)

    def test_breakout_without_pullback_is_rejected(self):
        prices = []
        for index in range(20):
            close = 100 + (index * 0.05)
            prices.append((close - 0.03, close + 0.05, close - 0.05, close, 1000))
        prices.extend([
            (100.95, 101.40, 100.90, 101.30, 1800),
            (101.30, 101.50, 101.25, 101.45, 1500),
        ])
        trades = server.simulate_intraday_strategy(
            self.candles(prices), "US", "TEST", "Test", 1,
        )
        self.assertEqual(trades, [])

    def test_flat_high_rank_signal_is_rejected(self):
        trades = server.simulate_intraday_strategy(
            self.candles([(100, 100.1, 99.9, 100, 1000)] * 30),
            "US", "TEST", "Test", 1,
        )
        self.assertEqual(trades, [])

    def test_same_minute_target_and_stop_assumes_stop_first(self):
        both = self.expand_three_minute_bars(
            [(100.75, 103.20, 100.00, 102.00, 4200)]
        )
        tail = self.expand_three_minute_bars(
            [(102.00, 102.10, 101.80, 102.00, 3000)]
        )
        trades = server.simulate_intraday_strategy(
            self.candles(self.qualifying_prefix() + both + tail),
            "US", "TEST", "Test", 1,
        )
        self.assertTrue(trades)
        self.assertEqual(trades[0]["exitKind"], "손실선")
        self.assertLessEqual(trades[0]["grossReturnRate"], server.PAPER_STOP_RATE)

    def test_net_two_r_target_exits_full_position(self):
        target = self.expand_three_minute_bars(
            [(100.75, 103.20, 100.50, 103.00, 4200)]
        )
        tail = self.expand_three_minute_bars(
            [(103.00, 103.10, 102.90, 103.00, 3000)]
        )
        trades = server.simulate_intraday_strategy(
            self.candles(self.qualifying_prefix() + target + tail),
            "US", "TEST", "Test", 1,
        )
        self.assertTrue(trades)
        self.assertEqual(trades[0]["exitKind"], "목표")
        self.assertGreater(trades[0]["grossReturnRate"], 0)

    def test_us_session_uses_exchange_date_across_korean_midnight(self):
        first = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
        second = datetime(2026, 7, 20, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(
            server.intraday_regular_session_key("US", first),
            server.intraday_regular_session_key("US", second),
        )

    def test_leveraged_products_are_not_replayed_as_standard_entries(self):
        trades = server.simulate_intraday_strategy(
            self.candles(self.qualifying_prefix() + [(101.3, 102, 101, 101.7, 1200)]),
            "US", "TQQQ", "TQQQ", 1,
        )
        self.assertEqual(trades, [])

    def test_worker_resource_budget_is_bounded(self):
        self.assertLessEqual(server.INTRADAY_BACKTEST_BATCH_PER_MARKET, 1)
        self.assertLessEqual(server.INTRADAY_BACKTEST_CANDLE_PAGES, 2)
        self.assertLessEqual(server.INTRADAY_BACKTEST_HISTORY_LIMIT, 1200)
        # The bounded replay now runs in toss-research.service, never inside
        # the long-lived dashboard process.
        self.assertFalse(server.INTRADAY_BACKTEST_AUTO_ENABLED)
        self.assertFalse(server.OFF_MARKET_STUDY_AUTO_ENABLED)
        self.assertFalse(server.DOMESTIC_DAY_REVIEW_AUTO_ENABLED)

    def test_legacy_raw_trades_are_compacted_without_losing_summary(self):
        study, changed = server.compact_intraday_backtest_record({
            "version": "minute-replay-v1",
            "tradeCount": 536,
            "metrics": {"profitFactor": 0.41},
            "trades": [{"id": "a"}, {"id": "b"}],
        })
        self.assertTrue(changed)
        self.assertEqual(study["trades"], [])
        self.assertEqual(study["tradeCount"], 536)
        self.assertEqual(study["metrics"]["profitFactor"], 0.41)

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
