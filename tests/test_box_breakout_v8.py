import unittest

import server


class BoxBreakoutRetestV8Tests(unittest.TestCase):
    def bars(self):
        return [
            {"startedAt": 1, "open": 100.00, "high": 100.20, "low": 99.80,
             "close": 100.00, "turnover": 3000},
            {"startedAt": 2, "open": 100.00, "high": 100.15, "low": 99.85,
             "close": 100.05, "turnover": 3000},
            {"startedAt": 3, "open": 100.05, "high": 100.18, "low": 99.82,
             "close": 99.98, "turnover": 3000},
            {"startedAt": 4, "open": 99.98, "high": 100.16, "low": 99.84,
             "close": 100.03, "turnover": 3000},
            {"startedAt": 5, "open": 100.03, "high": 100.17, "low": 99.83,
             "close": 100.01, "turnover": 3000},
            {"startedAt": 6, "open": 100.01, "high": 100.80, "low": 100.00,
             "close": 100.60, "turnover": 4800},
            {"startedAt": 7, "open": 100.60, "high": 100.65, "low": 100.15,
             "close": 100.30, "turnover": 2400},
            {"startedAt": 8, "open": 100.30, "high": 100.85, "low": 100.25,
             "close": 100.75, "turnover": 3300},
        ]

    def item(self):
        return {
            "symbol": "TEST",
            "name": "Test",
            "rank": 1,
            "sourcePrice": 100.75,
            "marketCountry": "US",
        }

    def regime(self, allowed=True):
        return {
            "allowed": allowed,
            "marketAllowed": allowed,
            "sectorAllowed": allowed,
            "rule": "TEST",
        }

    def test_exact_breakout_retest_confirmation_is_allowed(self):
        evidence = server.box_breakout_retest_snapshot(
            self.bars(), self.item(), "US", self.regime()
        )
        self.assertTrue(evidence["allowed"])
        self.assertEqual(evidence["phase"], "BREAKOUT_RETEST_CONFIRMED")
        self.assertEqual(evidence["box"]["minutes"], 15)
        self.assertTrue(evidence["breakoutChecks"]["volumeExpanded"])
        self.assertTrue(evidence["retestChecks"]["volumeContracted"])
        self.assertTrue(evidence["confirmationChecks"]["volumeReturned"])

    def test_breakout_without_volume_is_rejected(self):
        bars = self.bars()
        bars[-3]["turnover"] = 4000
        evidence = server.box_breakout_retest_snapshot(
            bars, self.item(), "US", self.regime()
        )
        self.assertFalse(evidence["allowed"])
        self.assertEqual(evidence["phase"], "BREAKOUT_WAIT")

    def test_market_or_sector_gate_cannot_be_bypassed(self):
        evidence = server.box_breakout_retest_snapshot(
            self.bars(), self.item(), "US", self.regime(False)
        )
        self.assertFalse(evidence["allowed"])
        self.assertEqual(evidence["phase"], "REGIME_BLOCKED")

    def test_leveraged_product_is_rejected(self):
        item = {**self.item(), "symbol": "TQQQ", "name": "TQQQ"}
        evidence = server.box_breakout_retest_snapshot(
            self.bars(), item, "US", self.regime()
        )
        self.assertFalse(evidence["allowed"])
        self.assertEqual(evidence["phase"], "PRODUCT_BLOCKED")

    def test_same_confirmation_bar_is_emitted_once(self):
        evidence = server.box_breakout_retest_snapshot(
            self.bars(), self.item(), "US", self.regime(), last_signal_bar=8
        )
        self.assertFalse(evidence["allowed"])
        self.assertEqual(evidence["phase"], "ALREADY_EMITTED")


if __name__ == "__main__":
    unittest.main()
