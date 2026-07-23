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

    def test_leader_requires_persistent_strength_and_turnover(self):
        history = {}
        started = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)
        leader = None
        for index in range(4):
            rows = self.universe(turnover=1000 + index * 100)
            server.apply_relative_strength_confirmation(
                rows,
                "KR",
                now=started + timedelta(seconds=index * 10),
                history=history,
            )
            leader = next(item for item in rows if item["symbol"] == "LEADER")
        evidence = leader["relativeStrengthEvidence"]
        self.assertTrue(evidence["allowed"])
        self.assertEqual(evidence["observationCount"], 4)
        self.assertTrue(evidence["turnoverRising"])
        self.assertTrue(evidence["topQuartileHeld"])

    def test_single_snapshot_is_not_an_entry_signal(self):
        rows = self.universe()
        server.apply_relative_strength_confirmation(
            rows,
            "KR",
            now=datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
            history={},
        )
        leader = next(item for item in rows if item["symbol"] == "LEADER")
        self.assertFalse(leader["relativeStrengthEvidence"]["allowed"])
        self.assertIn("지속 확인 1/4회", leader["relativeStrengthEvidence"]["reason"])

    def test_market_laggard_never_passes_relative_gate(self):
        history = {}
        started = datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)
        laggard = None
        for index in range(4):
            rows = self.universe(turnover=1000 + index * 100)
            server.apply_relative_strength_confirmation(
                rows,
                "KR",
                now=started + timedelta(seconds=index * 10),
                history=history,
            )
            laggard = next(item for item in rows if item["symbol"] == "TEST3")
        self.assertFalse(laggard["relativeStrengthEvidence"]["allowed"])

    def test_score_cannot_bypass_relative_strength_confirmation(self):
        candidate = {
            "marketCountry": "KR",
            "sourcePrice": 10000,
            "dailyRate": 0.03,
            "scoreComponents": {"liquidity": 40, "momentum": 35, "stability": 25},
            "relativeStrengthEvidence": {
                "allowed": False,
                "relativeStrength": 0.01,
                "reason": "상대강도 지속 확인 1/4회",
            },
        }
        result = server.apply_global_score_to_candidate(
            candidate, server.default_global_score_model()
        )
        self.assertEqual(result["verdict"], "관찰")
        self.assertFalse(result["entryGatesPassed"])


if __name__ == "__main__":
    unittest.main()
