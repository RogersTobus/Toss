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
        return {
            "symbol": "TEST",
            "name": "테스트",
            "lastPrice": price,
            "sourcePrice": price,
            "rank": 1,
            "marketCountry": "KR",
            "score": 90,
            "baseScore": 90,
            "verdict": "정밀 분석",
            "scoreFeatures": {"liquidity": 1, "momentum": 1, "stability": 1},
            "entryPatternEvidence": {
                "allowed": True,
                "engineVersion": server.PAPER_STRATEGY_ENGINE_VERSION,
                "boxHigh": 99.8,
                "signalBarStartedAt": 1000,
                "latestCompletedBar": {"startedAt": 1000, "close": price},
            },
        }

    @patch("server.market_minutes_to_close", return_value=120)
    def test_unlimited_signal_sample_is_separate_from_capital(self, _close):
        summary = server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        self.assertEqual(summary["activeCount"], len(server.SHADOW_EXIT_VARIANTS))
        self.assertTrue(summary["excludedFromCapitalLedger"])
        state = server.load_shadow_paper_state()
        self.assertTrue(state["samples"][0]["excludedFromBillionGoal"])
        self.assertEqual(
            {item["experimentVariant"] for item in state["samples"]},
            set(server.SHADOW_EXIT_VARIANTS),
        )
        server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        self.assertEqual(
            len(server.load_shadow_paper_state()["samples"]),
            len(server.SHADOW_EXIT_VARIANTS),
        )

    @patch("server.market_minutes_to_close", return_value=120)
    def test_stop_and_cost_are_recorded(self, _close):
        server.update_shadow_paper([self.candidate()], "KR", "KR 정규장")
        summary = server.update_shadow_paper([self.candidate(99.0)], "KR", "KR 정규장")
        self.assertEqual(summary["activeCount"], 0)
        sample = next(
            item for item in server.load_shadow_paper_state()["samples"]
            if item["experimentVariant"] == server.SHADOW_MAIN_EXIT_VARIANT
        )
        self.assertEqual(sample["exitKind"], "손실선")
        self.assertLess(sample["netReturnRate"], server.PAPER_STOP_RATE)
        self.assertAlmostEqual(sample["maximumFavorableExcursionRate"], 0.0)
        self.assertAlmostEqual(sample["maximumAdverseExcursionRate"], -0.01)

    def test_non_regular_session_does_not_create_samples(self):
        summary = server.update_shadow_paper([self.candidate()], "US", "US 데이마켓")
        self.assertEqual(summary["sampleCount"], 0)
        self.assertEqual(summary["activeCount"], 0)

    @patch("server.market_minutes_to_close", return_value=120)
    def test_v8_net_two_r_runs_beside_v7_baseline(self, _close):
        candidate = self.candidate()
        candidate["v7EntryPatternEvidence"] = {"allowed": True}
        server.update_shadow_paper([candidate], "KR", "KR 정규장")

        candidate = self.candidate(101.1)
        candidate["v7EntryPatternEvidence"] = {"allowed": True}
        server.update_shadow_paper([candidate], "KR", "KR 정규장")
        samples = server.load_shadow_paper_state()["samples"]
        current = next(
            item for item in samples
            if item["engineVersion"] == server.PAPER_STRATEGY_ENGINE_VERSION
        )
        baseline = next(
            item for item in samples
            if item["engineVersion"] == server.PAPER_BASELINE_ENGINE_VERSION
        )
        self.assertEqual(current["status"], "OPEN")
        self.assertTrue(baseline["partialTaken"])
        self.assertGreater(current["targetRate"], server.PAPER_TARGET_RATE)

        candidate = self.candidate(102.0)
        candidate["v7EntryPatternEvidence"] = {"allowed": True}
        server.update_shadow_paper([candidate], "KR", "KR 정규장")
        current = next(
            item for item in server.load_shadow_paper_state()["samples"]
            if item["engineVersion"] == server.PAPER_STRATEGY_ENGINE_VERSION
        )
        self.assertEqual(current["status"], "CLOSED")
        self.assertEqual(current["exitKind"], "목표")

    def test_v8_two_r_target_ignores_legacy_editable_target(self):
        expected = (
            server.RESEARCH_ROUND_TRIP_COST["KR"]
            + server.SHADOW_NET_REWARD_RISK
            * (
                abs(server.PAPER_STOP_RATE)
                + server.RESEARCH_ROUND_TRIP_COST["KR"]
            )
        )
        self.assertAlmostEqual(
            server.shadow_exit_target_rate(
                "KR", server.SHADOW_MAIN_EXIT_VARIANT, 0.05
            ),
            expected,
        )

    def test_stale_cross_market_position_is_closed_at_last_observed_price(self):
        state = server.new_shadow_paper_state()
        state["samples"] = [
            {
                "status": "OPEN",
                "market": "US",
                "symbol": "TEST",
                "openedAt": "2026-07-22T23:00:00+0900",
                "entryPrice": 100.0,
                "lastObservedPrice": 100.4,
                "engineVersion": server.PAPER_STRATEGY_ENGINE_VERSION,
            }
        ]
        server.save_shadow_paper_state(state)
        summary = server.update_shadow_paper(
            [],
            "KR",
            "시장 휴장",
            now=datetime.fromisoformat("2026-07-24T10:00:00+09:00"),
        )
        self.assertEqual(summary["activeCount"], 0)
        sample = server.load_shadow_paper_state()["samples"][0]
        self.assertEqual(sample["exitKind"], "마감청산")
        self.assertTrue(sample["stalePositionRecovered"])
        self.assertEqual(
            sample["fillPolicy"], "LAST_OBSERVED_SESSION_CLOSE_FALLBACK"
        )

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

    def test_current_strategy_summary_is_split_by_market(self):
        state = server.new_shadow_paper_state()
        state["samples"] = [
            {
                "status": "CLOSED",
                "market": market,
                "engineVersion": server.PAPER_STRATEGY_ENGINE_VERSION,
                "closedAt": closed_at,
                "exitKind": exit_kind,
                "netReturnRate": rate,
            }
            for market, closed_at, exit_kind, rate in (
                ("KR", "2026-07-23T14:00:00+0900", "손실선", -0.006),
                ("US", "2026-07-24T03:00:00+0900", "추적손절", 0.004),
            )
        ]
        current = server.shadow_paper_summary(state)["currentStrategy"]
        self.assertEqual(current["byMarket"]["KR"]["sampleCount"], 1)
        self.assertEqual(current["byMarket"]["US"]["sampleCount"], 1)
        self.assertEqual(current["exitKinds"]["손실선"], 1)

