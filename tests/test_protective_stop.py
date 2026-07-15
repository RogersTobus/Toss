import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class ProtectiveStopTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_paper_path = server.PAPER_PATH
        server.PAPER_PATH = Path(self.directory.name) / "paper_state.json"

    def tearDown(self):
        server.PAPER_PATH = self.original_paper_path
        self.directory.cleanup()

    def buy(self, price=100.0):
        return {
            "id": "BUY-1",
            "market": "US",
            "session": "US 프리마켓",
            "symbol": "TEST",
            "name": "테스트",
            "side": "BUY",
            "quantity": 10,
            "price": price,
            "currency": "KRW",
            "status": "FILLED",
            "createdAt": "2026-07-15T19:00:00+0900",
        }

    def test_buy_has_working_stop_at_minus_point_45_percent(self):
        order = self.buy()
        protective = server.build_protective_stop_order(order, -0.0045)
        self.assertEqual(protective["status"], "WORKING")
        self.assertEqual(protective["orderType"], "PAPER_PROTECTIVE_STOP")
        self.assertAlmostEqual(protective["stopRate"], -0.0045)
        self.assertAlmostEqual(protective["triggerPrice"], 99.55)

    def test_working_stop_is_repriced_when_strategy_rate_changes(self):
        order = self.buy()
        order["protectiveStopOrder"] = server.build_protective_stop_order(order, -0.005)
        protective, changed = server.ensure_protective_stop_order(order, -0.0045)
        self.assertTrue(changed)
        self.assertAlmostEqual(protective["stopRate"], -0.0045)
        self.assertAlmostEqual(protective["triggerPrice"], 99.55)

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.0045})
    @patch("server.refresh_position_prices", return_value={"TEST": 97.0})
    def test_gap_executes_pre_registered_paper_stop_at_trigger_price(self, _prices, _config):
        orders = [self.buy()]
        updated, changed = server.close_paper_positions_if_needed(
            {}, orders, [], "US", "US 프리마켓", stop_only=True
        )
        self.assertTrue(changed)
        self.assertEqual(len(updated), 2)
        protective = updated[0]["protectiveStopOrder"]
        exit_order = updated[1]
        self.assertEqual(protective["status"], "FILLED")
        self.assertAlmostEqual(exit_order["price"], 99.55)
        self.assertAlmostEqual(exit_order["returnRate"], -0.0045)
        self.assertAlmostEqual(exit_order["observedPrice"], 97.0)
        self.assertAlmostEqual(exit_order["observedReturnRate"], -0.03)
        self.assertEqual(exit_order["protectiveStopOrderId"], protective["id"])
        self.assertFalse(server.open_paper_positions(updated))
        journal_entry = {
            "exitKind": "손실선",
            "returnRate": exit_order["returnRate"],
            "stopRateAtExit": exit_order["stopRate"],
        }
        self.assertIsNone(server.journal_rule_violation(journal_entry, -0.0045))

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.0045})
    @patch("server.refresh_position_prices", return_value={"TEST": 101.2})
    def test_target_exit_cancels_the_working_stop(self, _prices, _config):
        buy = self.buy()
        buy["protectiveStopOrder"] = server.build_protective_stop_order(buy, -0.0045)
        updated, changed = server.close_paper_positions_if_needed(
            {}, [buy], [], "US", "US 프리마켓"
        )
        self.assertTrue(changed)
        self.assertEqual(updated[0]["protectiveStopOrder"]["status"], "CANCELLED")
        self.assertEqual(updated[1]["exitKind"], "목표")
        self.assertAlmostEqual(updated[1]["price"], 101.2)

    def test_stop_reentry_cooldown_only_blocks_recent_stop(self):
        now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
        orders = [
            {
                "side": "SELL",
                "status": "FILLED",
                "market": "US",
                "symbol": "RECENT",
                "exitKind": "손실선",
                "createdAt": (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            {
                "side": "SELL",
                "status": "FILLED",
                "market": "US",
                "symbol": "OLD",
                "exitKind": "손실선",
                "createdAt": (now - timedelta(seconds=61)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        ]
        blocked = server.stop_reentry_cooldown_symbols(orders, "US", now)
        self.assertEqual(blocked, {"RECENT"})


if __name__ == "__main__":
    unittest.main()
