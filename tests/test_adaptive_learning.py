import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class AdaptiveGlobalScoreTests(unittest.TestCase):
    def entry(self, momentum=1.0, liquidity=0.5, stability=0.5):
        return {
            "market": "KR",
            "symbol": "TEST",
            "name": "테스트 종목",
            "scoreFeatures": {
                "liquidity": liquidity,
                "momentum": momentum,
                "stability": stability,
            },
        }

    def test_default_score_matches_static_score(self):
        model = server.default_global_score_model()
        audit = server.global_score_audit(
            {"liquidity": 34, "momentum": 20, "stability": 25}, model
        )
        self.assertEqual(audit["baseScore"], 79.0)
        self.assertEqual(audit["adaptiveScore"], 79.0)
        self.assertEqual(audit["delta"], 0.0)

    def test_winner_strengthens_high_feature_for_all_symbols(self):
        model = server.default_global_score_model()
        revision = server.update_global_score_model(
            model, self.entry(momentum=1.0), 0.01, None, "BUY-1:SELL-1"
        )
        self.assertIsNotNone(revision)
        self.assertEqual(model["scope"], "GLOBAL_ALL_SYMBOLS")
        self.assertGreater(model["weights"]["momentum"], 1.0)
        self.assertLessEqual(
            model["weights"]["momentum"] - 1.0,
            server.GLOBAL_SCORE_MAX_TRADE_STEP,
        )
        policy = server.learning_entry_policy("OTHER-SYMBOL", 90, {"globalScoreModel": model})
        self.assertTrue(policy["allowed"])
        self.assertEqual(policy["scope"], "GLOBAL_ALL_SYMBOLS")

    def test_loser_weakens_high_feature_and_changes_are_clamped(self):
        model = server.default_global_score_model()
        for index in range(100):
            server.update_global_score_model(
                model,
                self.entry(momentum=1.0),
                -0.02,
                None,
                f"BUY-{index}:SELL-{index}",
            )
        self.assertGreaterEqual(model["weights"]["momentum"], server.GLOBAL_SCORE_WEIGHT_MIN)
        self.assertLess(model["weights"]["momentum"], 1.0)
        self.assertEqual(model["entryThreshold"], 83)

    def test_missing_old_features_do_not_invent_learning(self):
        model = server.default_global_score_model()
        revision = server.update_global_score_model(
            model, {"symbol": "OLD"}, -0.01, None, "OLD-BUY:OLD-SELL"
        )
        self.assertIsNone(revision)
        self.assertEqual(model["sampleCount"], 0)

    def test_closed_trade_is_absorbed_once_into_global_brain(self):
        orders = [
            {
                "id": "BUY-ONCE",
                "market": "KR",
                "symbol": "005930",
                "name": "삼성전자",
                "side": "BUY",
                "quantity": 1,
                "price": 100000,
                "createdAt": "2026-07-15T10:00:00+0900",
                "entryScore": 88,
                "scoreFeatures": {"liquidity": 0.9, "momentum": 1.0, "stability": 1.0},
            },
            {
                "id": "SELL-ONCE",
                "entryOrderId": "BUY-ONCE",
                "market": "KR",
                "symbol": "005930",
                "side": "SELL",
                "quantity": 1,
                "entryPrice": 100000,
                "price": 101000,
                "returnRate": 0.01,
                "profit": 1000,
                "stopRate": -0.005,
                "createdAt": "2026-07-15T10:04:00+0900",
            },
        ]
        original_path = server.LEARNING_PATH
        try:
            with TemporaryDirectory() as directory:
                server.LEARNING_PATH = Path(directory) / "learning_state.json"
                first = server.sync_learning_brain(orders, {})
                second = server.sync_learning_brain(orders, {})
                self.assertEqual(first["globalScoreModel"]["sampleCount"], 1)
                self.assertEqual(second["globalScoreModel"]["sampleCount"], 1)
                self.assertEqual(len(second["scoreModelProcessedTrades"]), 1)
                self.assertEqual(second["globalScoreModel"]["scope"], "GLOBAL_ALL_SYMBOLS")
        finally:
            server.LEARNING_PATH = original_path


class OffMarketResearchTests(unittest.TestCase):
    def candles(self, count=180):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        rows = []
        price = 100.0
        for index in range(count):
            drift = 0.004 if (index // 15) % 2 == 0 else -0.001
            price *= 1 + drift
            rows.append(
                {
                    "timestamp": (start + timedelta(days=index)).isoformat(),
                    "open": price * 0.995,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1000 + ((index % 12) * 130),
                }
            )
        return rows

    def test_daily_weekly_monthly_research_produces_auditable_data(self):
        daily = self.candles(600)
        analyses = []
        for timeframe in ("1d", "1w", "1mo"):
            candles = server.aggregate_study_candles(daily, timeframe)
            patterns = server.study_pattern_observations(candles, timeframe)
            backtest = server.study_backtest(candles, timeframe, 1)
            analyses.append(
                {
                    "market": "KR",
                    "symbol": "005930",
                    "name": "삼성전자",
                    "timeframe": timeframe,
                    "technical": server.study_technical_snapshot(candles),
                    "patterns": patterns[:8],
                    "patternObservationCount": sum(int(item.get("count") or 0) for item in patterns),
                    "backtest": backtest,
                }
            )
            self.assertEqual(backtest["researchPass"], "균형형")
        pattern_summary = server.summarize_study_patterns(analyses)
        backtest_summary = server.summarize_off_market_backtests(analyses)
        symbol_catalog = server.build_symbol_study_catalog(analyses)
        self.assertGreater(pattern_summary["observationCount"], 100)
        self.assertGreater(pattern_summary["uniquePatternCount"], 0)
        self.assertGreater(backtest_summary["analysisCount"], 0)
        self.assertEqual(len(symbol_catalog), 1)
        self.assertEqual(symbol_catalog[0]["symbol"], "005930")
        self.assertEqual(symbol_catalog[0]["completeTimeframeCount"], 3)
        self.assertEqual(
            [item["label"] for item in symbol_catalog[0]["timeframes"]],
            ["일봉", "주봉", "월봉"],
        )

    def test_backtest_influence_is_lower_than_real_trade_step(self):
        model = server.default_global_score_model()
        summary = {
            "tradeCount": 90,
            "timeframes": {
                "1d": {"tradeCount": 30, "winRate": 0.65, "averageReturn": 0.01},
                "1w": {"tradeCount": 30, "winRate": 0.60, "averageReturn": 0.02},
                "1mo": {"tradeCount": 30, "winRate": 0.50, "averageReturn": 0.00},
            },
        }
        influence = server.apply_off_market_backtest_influence(model, summary, "STUDY-1")
        self.assertTrue(influence["applied"])
        self.assertLessEqual(abs(influence["delta"]), 0.01)
        self.assertEqual(model["lastChange"]["scope"], "OFF_MARKET_BACKTEST")


if __name__ == "__main__":
    unittest.main()
