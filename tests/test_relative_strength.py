import unittest
from datetime import datetime, timedelta, timezone

import server


class RelativeStrengthConfirmationTests(unittest.TestCase):
    def universe(self, leader_rate=0.03, turnover=1000):
        rates = [-0.01, 0.0, 0.005, 0.01, 0.015, 0.02, 0.025]
        rows = [
            {
                "rank": index + 2,
                "symbol": f"TEST{index}",
                "dailyRate": rate,
                "tradingAmount": turnover + index,
            }
            for index, rate in enumerate(rates)
        ]
        rows.append(
            {
                "rank": 1,
                "symbol": "LEADER",
                "dailyRate": leader_rate,
                "tradingAmount": turnover,
            }
        )
        return rows

    def run_leader_path(self, rates):
        history = {}
        started = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)
        leader = None
        for index, rate in enumerate(rates):
            rows = self.universe(
                leader_rate=rate,
                turnover=1000 + index * 100,
            )
            server.apply_pullback_resumption_confirmation(
                rows,
                "KR",
                now=started + timedelta(seconds=index * 10),
                history=history,
            )
            leader = next(item for item in rows if item["symbol"] == "LEADER")
        return leader, history

    def test_leader_then_pullback_then_resumption_is_confirmed(self):
        leader, history = self.run_leader_path([0.030, 0.032, 0.028, 0.0305])
        evidence = leader["entryPatternEvidence"]
        self.assertTrue(evidence["allowed"])
        self.assertEqual(evidence["phase"], "RESUMPTION_CONFIRMED")
        self.assertAlmostEqual(evidence["pullbackDepth"], 0.004)
        self.assertAlmostEqual(evidence["recoveryRate"], 0.0025)
        self.assertTrue(evidence["turnoverRising"])
        breadth = leader["marketBreadthEvidence"]
        self.assertIn(breadth["regime"], ("확산 상승", "선별 상승", "하락 우세"))
        self.assertFalse(breadth["directGate"])

        rows = self.universe(leader_rate=0.031, turnover=1500)
        server.apply_pullback_resumption_confirmation(
            rows,
            "KR",
            now=datetime(2026, 7, 23, 1, 0, 40, tzinfo=timezone.utc),
            history=history,
        )
        next_scan = next(item for item in rows if item["symbol"] == "LEADER")
        self.assertFalse(next_scan["entryPatternEvidence"]["allowed"])

    def test_us_uses_wider_pullback_and_recovery_thresholds(self):
        history = {}
        started = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
        leader = None
        for index, rate in enumerate((0.040, 0.043, 0.037, 0.040)):
            rows = self.universe(leader_rate=rate, turnover=2000 + index * 100)
            server.apply_pullback_resumption_confirmation(
                rows,
                "US",
                now=started + timedelta(seconds=index * 10),
                history=history,
            )
            leader = next(item for item in rows if item["symbol"] == "LEADER")
        evidence = leader["entryPatternEvidence"]
        self.assertTrue(evidence["allowed"])
        self.assertAlmostEqual(evidence["minimumPullback"], 0.003)
        self.assertAlmostEqual(evidence["minimumRecovery"], 0.002)

    def test_single_snapshot_is_not_an_entry_signal(self):
        rows = self.universe()
        server.apply_pullback_resumption_confirmation(
            rows,
            "KR",
            now=datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
            history={},
        )
        leader = next(item for item in rows if item["symbol"] == "LEADER")
        self.assertFalse(leader["entryPatternEvidence"]["allowed"])
        self.assertEqual(
            leader["entryPatternEvidence"]["phase"], "WATCHING_LEADER"
        )

    def test_leader_without_pullback_never_becomes_an_entry(self):
        leader, _ = self.run_leader_path([0.030, 0.032, 0.034, 0.036])
        evidence = leader["entryPatternEvidence"]
        self.assertFalse(evidence["allowed"])
        self.assertEqual(evidence["phase"], "LEADER_CONFIRMED")

    def test_too_deep_pullback_resets_the_cycle(self):
        leader, _ = self.run_leader_path([0.030, 0.032, 0.019, 0.022])
        evidence = leader["entryPatternEvidence"]
        self.assertFalse(evidence["allowed"])
        self.assertNotEqual(evidence["phase"], "RESUMPTION_CONFIRMED")

    def test_market_laggard_never_passes_price_path_gate(self):
        history = {}
        started = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)
        laggard = None
        for index in range(4):
            rows = self.universe(turnover=1000 + index * 100)
            server.apply_pullback_resumption_confirmation(
                rows,
                "KR",
                now=started + timedelta(seconds=index * 10),
                history=history,
            )
            laggard = next(item for item in rows if item["symbol"] == "TEST3")
        self.assertFalse(laggard["entryPatternEvidence"]["allowed"])

    def test_score_cannot_bypass_pullback_resumption_confirmation(self):
        candidate = {
            "marketCountry": "KR",
            "sourcePrice": 10000,
            "dailyRate": 0.03,
            "scoreComponents": {"liquidity": 40, "momentum": 35, "stability": 25},
            "entryPatternEvidence": {
                "allowed": False,
                "relativeStrength": 0.01,
                "phase": "LEADER_CONFIRMED",
                "reason": "선도 확인 · 눌림 대기",
            },
        }
        result = server.apply_global_score_to_candidate(
            candidate, server.default_global_score_model()
        )
        self.assertEqual(result["verdict"], "관찰")
        self.assertFalse(result["entryGatesPassed"])


if __name__ == "__main__":
    unittest.main()
