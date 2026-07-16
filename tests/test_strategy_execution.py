import time
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_overlapping_kr_and_us_sessions_are_both_active(self):
        original_cache = dict(server.CALENDAR_CACHE)
        moment = datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)
        server.CALENDAR_CACHE.update(
            {
                "expiresAt": time.time() + 60,
                "KR": {
                    "today": {
                        "integrated": {
                            "regularMarket": {
                                "startTime": "2026-07-16T00:00:00+00:00",
                                "endTime": "2026-07-16T06:30:00+00:00",
                            }
                        }
                    }
                },
                "US": {
                    "today": {
                        "dayMarket": {
                            "startTime": "2026-07-16T00:00:00+00:00",
                            "endTime": "2026-07-16T08:00:00+00:00",
                        }
                    }
                },
            }
        )
        try:
            self.assertEqual(
                server.active_market_sessions({}, moment),
                [("KR", "KR 정규장"), ("US", "US 데이마켓")],
            )
        finally:
            server.CALENDAR_CACHE.clear()
            server.CALENDAR_CACHE.update(original_cache)

    @patch("server.close_paper_positions_if_needed", return_value=([], False))
    @patch("server.load_paper_orders", return_value=[])
    def test_risk_monitor_checks_every_active_market(self, _orders, close_positions):
        sessions = [("KR", "KR 정규장"), ("US", "US 데이마켓")]
        _, changed, active = server.monitor_active_position_risks({}, sessions)
        self.assertFalse(changed)
        self.assertEqual(active, sessions)
        self.assertEqual(close_positions.call_count, 2)
        self.assertEqual(close_positions.call_args_list[0].args[3], "KR")
        self.assertEqual(close_positions.call_args_list[1].args[3], "US")

    def test_recent_exit_cooldown_includes_time_and_target_exits(self):
        now = datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)
        orders = [
            {
                "side": "SELL",
                "status": "FILLED",
                "market": "KR",
                "symbol": "TIME",
                "exitKind": "시간청산",
                "createdAt": (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            {
                "side": "SELL",
                "status": "FILLED",
                "market": "KR",
                "symbol": "TARGET",
                "exitKind": "목표",
                "createdAt": (now - timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        ]
        self.assertEqual(
            server.recent_exit_cooldown_symbols(
                orders, "KR", now=now, cooldown_seconds=600
            ),
            {"TIME", "TARGET"},
        )

    def test_candidate_rotation_prefers_least_sampled_then_best_score(self):
        candidates = [
            {"symbol": "OVERUSED", "score": 99, "rank": 1},
            {"symbol": "FRESH_LOW", "score": 84, "rank": 3},
            {"symbol": "FRESH_HIGH", "score": 90, "rank": 2},
        ]
        ranked = server.rank_candidates_for_sample_diversity(
            candidates, {"OVERUSED": 12, "FRESH_LOW": 0, "FRESH_HIGH": 0}
        )
        self.assertEqual(
            [item["symbol"] for item in ranked],
            ["FRESH_HIGH", "FRESH_LOW", "OVERUSED"],
        )

    def test_today_trade_stats_and_operation_report_include_win_rate(self):
        trades = [
            {
                "status": "CLOSED",
                "closedAt": "2026-07-16T10:00:00+0900",
                "returnRate": 0.01,
            },
            {
                "status": "CLOSED",
                "closedAt": "2026-07-16T10:01:00+0900",
                "returnRate": -0.0045,
            },
            {
                "status": "CLOSED",
                "closedAt": "2026-07-16T10:02:00+0900",
                "returnRate": 0.0,
            },
            {
                "status": "OPEN",
                "openedAt": "2026-07-16T10:03:00+0900",
                "returnRate": 0.02,
            },
        ]
        stats = server.trade_outcome_stats(trades, "2026-07-16")
        self.assertEqual(stats["closedCount"], 3)
        self.assertEqual(stats["winCount"], 1)
        self.assertEqual(stats["lossCount"], 1)
        self.assertEqual(stats["flatCount"], 1)
        self.assertAlmostEqual(stats["winRate"], 1 / 3)

        message = server.build_operation_report(
            "KR",
            "KR 정규장",
            [],
            [],
            {
                "periodReturns": {"today": {"profitKrw": 1000, "returnRate": 0.001}},
                "todayTradeStats": stats,
                "openPositionCount": 0,
                "todayOrderCount": 3,
                "decision": {},
            },
        )
        self.assertIn("현재 승률: 33.3% (1승 · 1패 · 1보합 / 3건)", message)


if __name__ == "__main__":
    unittest.main()
