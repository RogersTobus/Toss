import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class StrategyExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_strategy_path = server.STRATEGY_CONFIG_PATH
        self.original_paper_path = server.PAPER_PATH
        server.STRATEGY_CONFIG_PATH = Path(self.directory.name) / "strategy_config.json"
        server.PAPER_PATH = Path(self.directory.name) / "paper_state.json"

    def tearDown(self):
        server.STRATEGY_CONFIG_PATH = self.original_strategy_path
        server.PAPER_PATH = self.original_paper_path
        self.directory.cleanup()

    def test_editable_copy_becomes_versioned_runtime_parameters(self):
        strategies = server.normalize_strategies()
        updates = {
            "hard-stop-loss": "매수 즉시 -0.60% 예약 보호매도",
            "profit-trailing": "+1.20% 도달 시 수익 청산",
            "score-entry-80": "83점 이상만 진입",
            "three-minute-exit": "5분 안에 상승 없으면 청산",
            "reentry-cooldown": "2회 연속 손절 시 15분 대기",
        }
        for strategy in strategies:
            if strategy["id"] in updates:
                strategy["judge"] = updates[strategy["id"]]

        first = server.save_strategy_config({"strategies": strategies})
        first_policy = server.strategy_execution_policy(first)
        params = first_policy["parameters"]
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first_policy["effectiveFrom"], "NEXT_ENTRY")
        self.assertAlmostEqual(params["stopRate"], -0.006)
        self.assertAlmostEqual(params["targetRate"], 0.012)
        self.assertEqual(params["entryScoreFloor"], 83)
        self.assertEqual(params["timeExitSeconds"], 300)
        self.assertEqual(params["reentryCooldownSeconds"], 900)

        second = server.save_strategy_config({"strategies": strategies})
        self.assertEqual(second["revision"], 2)

    @patch("server.refresh_position_prices", return_value={"TEST": 100.0})
    def test_entry_strategy_snapshot_controls_time_exit(self, _prices):
        opened = (datetime.now().astimezone() - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S%z")
        buy = {
            "id": "BUY-TIME",
            "market": "US",
            "session": "US 프리마켓",
            "symbol": "TEST",
            "name": "테스트",
            "side": "BUY",
            "quantity": 1,
            "price": 100.0,
            "status": "FILLED",
            "createdAt": opened,
            "strategyIds": ["three-minute-exit"],
            "strategyRevision": 7,
            "strategyExecution": {
                "revision": 7,
                "enabledIds": ["three-minute-exit"],
                "parameters": {
                    "timeExitSeconds": 60,
                    "timeExitMinimumReturn": 0.001,
                    "targetRate": 0.01,
                    "stopRate": -0.0045,
                },
            },
        }
        orders, changed = server.close_paper_positions_if_needed(
            {}, [buy], [], "US", "US 프리마켓"
        )
        self.assertTrue(changed)
        self.assertEqual(orders[-1]["exitKind"], "시간청산")
        self.assertEqual(orders[-1]["strategyRevision"], 7)
        self.assertNotIn("protectiveStopOrder", orders[0])

    @patch("server.refresh_position_prices", return_value={"TEST": 90.0})
    def test_disabled_hard_stop_is_not_added_to_next_entry(self, _prices):
        buy = {
            "id": "BUY-NO-STOP",
            "market": "US",
            "symbol": "TEST",
            "side": "BUY",
            "quantity": 1,
            "price": 100.0,
            "status": "FILLED",
            "createdAt": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
            "strategyIds": [],
            "strategyExecution": {"revision": 8, "enabledIds": [], "parameters": {}},
        }
        orders, changed = server.close_paper_positions_if_needed(
            {}, [buy], [], "US", "US 프리마켓", stop_only=True
        )
        self.assertFalse(changed)
        self.assertEqual(len(orders), 1)
        self.assertNotIn("protectiveStopOrder", orders[0])

    def test_bounded_funding_policy_is_reflected_in_capital_summary(self):
        capital = server.paper_capital_summary(
            [], {"unlimitedFunding": False, "parameters": {}}
        )
        self.assertEqual(capital["allocationMode"], "bounded-paper-capital")
        self.assertEqual(capital["fundingLimit"], server.PAPER_STARTING_CAPITAL_KRW)
        self.assertEqual(capital["remainingDeployableKrw"], server.PAPER_STARTING_CAPITAL_KRW)
        self.assertFalse(capital["referenceOnly"])

        unlimited = server.paper_capital_summary(
            [], {"unlimitedFunding": True, "parameters": {}}
        )
        self.assertEqual(unlimited["fundingLimit"], "UNLIMITED")
        self.assertTrue(unlimited["referenceOnly"])


if __name__ == "__main__":
    unittest.main()
