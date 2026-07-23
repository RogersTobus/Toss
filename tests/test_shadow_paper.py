import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class ShadowPaperTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original = server.SHADOW_PAPER_PATH
        server.SHADOW_PAPER_PATH = Path(self.directory.name) / "shadow.json"

    def tearDown(self):
        server.SHADOW_PAPER_PATH = self.original
        self.directory.cleanup()

    def candidate(self, price=100.0):
        return {"symbol": "TEST", "name": "테스트", "lastPrice": price,
                "score": 90, "baseScore": 90, "verdict": "정밀 분석",
                "scoreFeatures": {"liquidity": 1, "momentum": 1, "stability": 1}}

    @patch("server.market_minutes_to_close", return_value=120)
    def test_unlimited_signal_sample_is_separate_from_capital(self, _close):
        summary = server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        self.assertEqual(summary["activeCount"], 1)
        self.assertTrue(summary["excludedFromCapitalLedger"])
        state = server.load_shadow_paper_state()
        self.assertTrue(state["samples"][0]["excludedFromBillionGoal"])
        server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        self.assertEqual(len(server.load_shadow_paper_state()["samples"]), 1)

    @patch("server.market_minutes_to_close", return_value=120)
    def test_stop_and_cost_are_recorded(self, _close):
        server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        summary = server.update_shadow_paper([self.candidate(99.0)], "KR", "KR 정규장")
        self.assertEqual(summary["activeCount"], 0)
        sample = server.load_shadow_paper_state()["samples"][0]
        self.assertEqual(sample["exitKind"], "손실선")
        self.assertLess(sample["netReturnRate"], server.PAPER_STOP_RATE)

    def test_non_regular_session_does_not_create_samples(self):
        summary = server.update_shadow_paper([self.candidate()], "US", "US 데이마켓")
        self.assertEqual(summary["sampleCount"], 0)
        self.assertEqual(summary["activeCount"], 0)

    def test_summary_keeps_kr_and_us_results_on_the_same_trading_day(self):
        state = server.new_shadow_paper_state()
        state["samples"] = [
            {
                "status": "CLOSED", "market": "KR",
                "closedAt": "2026-07-22T14:00:00+0900", "netReturnRate": 0.01,
            },
            {
                "status": "CLOSED", "market": "US",
                "closedAt": "2026-07-23T03:00:00+0900", "netReturnRate": -0.005,
            },
        ]
        summary = server.shadow_paper_summary(state)
        day = summary["recentDays"][0]
        self.assertEqual(day["tradingDay"], "2026-07-22")
        self.assertEqual(day["byMarket"]["KR"]["sampleCount"], 1)
        self.assertEqual(day["byMarket"]["US"]["sampleCount"], 1)

