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

    def test_buy_has_working_stop_at_minus_point_five_percent(self):
        order = self.buy()
        protective = server.build_protective_stop_order(order, -0.005)
        self.assertEqual(protective["status"], "WORKING")
        self.assertEqual(protective["orderType"], "PAPER_PROTECTIVE_STOP")
        self.assertAlmostEqual(protective["stopRate"], -0.005)
        self.assertAlmostEqual(protective["triggerPrice"], 99.5)

    def test_working_stop_is_repriced_when_strategy_rate_changes(self):
        order = self.buy()
        order["protectiveStopOrder"] = server.build_protective_stop_order(order, -0.0045)
        protective, changed = server.ensure_protective_stop_order(order, -0.005)
        self.assertTrue(changed)
        self.assertAlmostEqual(protective["stopRate"], -0.005)
        self.assertAlmostEqual(protective["triggerPrice"], 99.5)

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.0045})
    @patch("server.refresh_position_prices", return_value={})
    def test_existing_position_gets_protection_even_without_a_fresh_quote(self, _prices, _config):
        orders = [self.buy()]
        updated, changed = server.close_paper_positions_if_needed(
            {}, orders, [], "US", "US premarket", stop_only=True
        )
        self.assertTrue(changed)
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["protectiveStopOrder"]["status"], "WORKING")
        self.assertAlmostEqual(updated[0]["protectiveStopOrder"]["triggerPrice"], 99.5)

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.0045})
    @patch("server.refresh_position_prices", return_value={"TEST": 97.0})
    def test_gap_triggers_registered_stop_but_fills_at_observed_price(self, _prices, _config):
        orders = [self.buy()]
        updated, changed = server.close_paper_positions_if_needed(
            {}, orders, [], "US", "US 프리마켓", stop_only=True
        )
        self.assertTrue(changed)
        self.assertEqual(len(updated), 2)
        protective = updated[0]["protectiveStopOrder"]
        exit_order = updated[1]
        self.assertEqual(protective["status"], "FILLED")
        self.assertAlmostEqual(exit_order["price"], 97.0)
        self.assertAlmostEqual(exit_order["returnRate"], -0.03)
        self.assertAlmostEqual(exit_order["observedPrice"], 97.0)
        self.assertAlmostEqual(exit_order["observedReturnRate"], -0.03)
        self.assertEqual(exit_order["protectiveStopOrderId"], protective["id"])
        self.assertFalse(server.open_paper_positions(updated))
        journal_entry = {
            "status": "청산",
            "exitKind": "손실선",
            "returnRate": exit_order["returnRate"],
            "stopRateAtExit": exit_order["stopRate"],
        }
        self.assertIsNotNone(server.journal_rule_violation(journal_entry, -0.005))

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.0045})
    @patch("server.refresh_position_prices", return_value={"TEST": 101.2})
    def test_target_takes_half_and_arms_break_even_trailing_stop(self, _prices, _config):
        buy = self.buy()
        buy["protectiveStopOrder"] = server.build_protective_stop_order(buy, -0.0045)
        updated, changed = server.close_paper_positions_if_needed(
            {}, [buy], [], "US", "US 프리마켓"
        )
        self.assertTrue(changed)
        self.assertEqual(updated[0]["protectiveStopOrder"]["status"], "WORKING")
        self.assertEqual(updated[0]["protectiveStopOrder"]["mode"], "TRAILING")
        self.assertAlmostEqual(updated[0]["protectiveStopOrder"]["triggerPrice"], 100.694)
        self.assertAlmostEqual(updated[0]["remainingQuantity"], 5)
        self.assertTrue(updated[1]["partial"])
        self.assertEqual(updated[1]["exitKind"], "부분익절")
        self.assertAlmostEqual(updated[1]["price"], 101.2)
        self.assertIn("TEST", server.open_paper_positions(updated))

    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.005})
    @patch("server.refresh_position_prices", return_value={"TEST": 100.6})
    def test_trailing_stop_closes_remaining_half_and_ledger_combines_exits(self, _prices, _config):
        buy = self.buy()
        buy["remainingQuantity"] = 5
        buy["partialTakeProfit"] = {"status": "FILLED", "quantity": 5, "price": 101.2}
        buy["highWaterPrice"] = 101.2
        buy["protectiveStopOrder"] = {
            **server.build_protective_stop_order(buy, -0.005),
            "mode": "TRAILING",
            "orderType": "PAPER_TRAILING_STOP",
            "triggerPrice": 100.694,
            "quantity": 5,
        }
        partial = {
            "id": "PARTIAL-1", "entryOrderId": "BUY-1", "market": "US", "symbol": "TEST",
            "side": "SELL", "partial": True, "quantity": 5, "price": 101.2,
            "profit": 6, "createdAt": "2026-07-15T19:01:00+0900",
        }
        updated, changed = server.close_paper_positions_if_needed({}, [buy, partial], [], "US", "US 정규장", stop_only=True)
        self.assertTrue(changed)
        self.assertEqual(updated[-1]["exitKind"], "추적손절")
        self.assertFalse(server.open_paper_positions(updated))
        ledger = server.paper_trade_ledger(updated, {})
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["partialExitCount"], 1)
        self.assertAlmostEqual(ledger[0]["quantity"], 10)

    @patch("server.market_minutes_to_close", return_value=4)
    @patch("server.strategy_config", return_value={"targetRate": 0.01, "stopRate": -0.005})
    @patch("server.refresh_position_prices", return_value={"TEST": 100.2})
    def test_position_is_closed_five_minutes_before_regular_market_close(
        self, _prices, _config, _minutes
    ):
        updated, changed = server.close_paper_positions_if_needed(
            {}, [self.buy()], [], "US", "US 정규장"
        )
        self.assertTrue(changed)
        self.assertEqual(updated[-1]["exitKind"], "마감청산")
        self.assertFalse(server.open_paper_positions(updated))

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
