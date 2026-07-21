import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import server


class StrategyExecutionTests(unittest.TestCase):
    def test_research_runs_outside_both_regular_markets(self):
        self.assertFalse(server.regular_market_is_active([]))
        self.assertFalse(server.regular_market_is_active([("US", "US 데이마켓")]))
        self.assertTrue(server.regular_market_is_active([("KR", "KR 정규장")]))
        self.assertTrue(server.regular_market_is_active([("US", "US 정규장")]))
        self.assertEqual(server.markets_available_for_research([]), ("KR", "US"))
        self.assertEqual(
            server.markets_available_for_research([("KR", "KR 정규장")]),
            ("US",),
        )
        self.assertEqual(
            server.markets_available_for_research([("US", "US 정규장")]),
            ("KR",),
        )

    def test_research_catalog_covers_both_complete_listed_markets(self):
        self.assertGreater(len(server.listed_stock_universe("KR")), 2000)
        self.assertGreater(len(server.listed_stock_universe("US")), 10000)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_strategy_path = server.STRATEGY_CONFIG_PATH
        self.original_paper_path = server.PAPER_PATH
        server.STRATEGY_CONFIG_PATH = Path(self.directory.name) / "strategy_config.json"
        server.PAPER_PATH = Path(self.directory.name) / "paper_state.json"

    def test_default_copy_keeps_base_cooldown_and_position_cap(self):
        config = {
            **server.DEFAULT_STRATEGY_CONFIG,
            "strategies": server.normalize_strategies(),
        }
        parameters = server.strategy_runtime_parameters(config)
        self.assertEqual(parameters["reentryCooldownSeconds"], 600)
        self.assertEqual(parameters["maxAllocationRate"], 0.30)

    def test_risk_refresh_preserves_shadow_only_entry_decision(self):
        previous = {
            "decision": {"mode": "그림자 학습", "reason": "비용 후 음수"},
            "evidenceGate": {"mainEntryPaused": True},
            "learningDecisions": [{"allowed": False}],
        }
        merged = server.preserve_entry_policy_summary(
            {"decision": {"mode": "균형 모드"}, "openPositionCount": 0},
            previous,
        )
        self.assertEqual(merged["decision"]["mode"], "그림자 학습")
        self.assertTrue(merged["evidenceGate"]["mainEntryPaused"])
        self.assertEqual(len(merged["learningDecisions"]), 1)

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
        self.assertAlmostEqual(params["stopRate"], -0.005)
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
        self.assertEqual(orders[-1]["postExitStudy"]["status"], "TRACKING")
        self.assertEqual(
            set(orders[-1]["postExitStudy"]["horizons"]), {"5m", "10m", "30m"}
        )
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

    def test_live_paper_policy_cannot_enable_unlimited_sprint(self):
        strategies = server.normalize_strategies()
        for strategy in strategies:
            if strategy["id"] in ("paper-learning-sprint", "unlimited-paper-experience"):
                strategy["enabled"] = True
        policy = server.strategy_execution_policy(
            {**server.DEFAULT_STRATEGY_CONFIG, "strategies": strategies}
        )
        self.assertFalse(policy["learningSprint"])
        self.assertTrue(policy["unlimitedDailyEntries"])
        self.assertFalse(policy["dailyLossLockEnabled"])
        self.assertFalse(policy["dailyRisk"])
        self.assertFalse(policy["unlimitedFunding"])
        self.assertFalse(policy["unlimitedPositions"])

        rules = server.safety_rules(0.0, 0, 99, [], False, None, {
            **server.DEFAULT_STRATEGY_CONFIG,
            "strategies": strategies,
        })
        daily_entry_rule = next(rule for rule in rules if rule["key"] == "dailyOrders")
        self.assertEqual(daily_entry_rule["status"], "무제한")
        self.assertEqual(daily_entry_rule["tone"], "safe")

        daily_loss_rule = next(rule for rule in server.safety_rules(
            0.0,
            0,
            99,
            [],
            False,
            None,
            {**server.DEFAULT_STRATEGY_CONFIG, "strategies": strategies},
            daily_return=-0.02,
        ) if rule["key"] == "dailyLoss")
        self.assertEqual(daily_loss_rule["status"], "기록")
        self.assertEqual(daily_loss_rule["tone"], "safe")
        self.assertIn("거래 잠금 없음", daily_loss_rule["detail"])

    def test_two_consecutive_market_losses_start_ten_minute_cooldown(self):
        now = datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc)
        orders = [
            {
                "side": "SELL", "status": "FILLED", "market": "KR",
                "symbol": "A", "returnRate": -0.005,
                "createdAt": (now - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            {
                "side": "SELL", "status": "FILLED", "market": "KR",
                "symbol": "B", "returnRate": -0.004,
                "createdAt": (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
        ]
        cooldown = server.market_loss_streak_cooldown(orders, "KR", now)
        self.assertTrue(cooldown["blocked"])
        self.assertEqual(cooldown["consecutiveLosses"], 2)
        self.assertEqual(cooldown["remainingSeconds"], 570)

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

    @patch("server.update_post_exit_studies_if_due", return_value=([], False))
    @patch("server.close_paper_positions_if_needed", return_value=([], False))
    @patch("server.load_paper_orders", return_value=[])
    def test_risk_monitor_checks_every_active_market(
        self, _orders, close_positions, update_studies
    ):
        sessions = [("KR", "KR 정규장"), ("US", "US 데이마켓")]
        original_results = list(server.ANALYSIS.get("results") or [])
        kr_result = {"symbol": "KR1", "marketCountry": "KR", "lastPrice": 100.0}
        us_result = {"symbol": "US1", "marketCountry": "US", "lastPrice": 200.0}
        try:
            server.ANALYSIS["results"] = [kr_result, us_result]
            _, changed, active = server.monitor_active_position_risks({}, sessions)
            self.assertFalse(changed)
            self.assertEqual(active, sessions)
            self.assertEqual(close_positions.call_count, 2)
            self.assertEqual(close_positions.call_args_list[0].args[2], [kr_result])
            self.assertEqual(close_positions.call_args_list[0].args[3], "KR")
            self.assertEqual(close_positions.call_args_list[1].args[2], [us_result])
            self.assertEqual(close_positions.call_args_list[1].args[3], "US")
            self.assertEqual(update_studies.call_args_list[0].args[3], [kr_result])
            self.assertEqual(update_studies.call_args_list[1].args[3], [us_result])
        finally:
            server.ANALYSIS["results"] = original_results

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

    def test_us_day_market_switches_to_domestic_review_only(self):
        config = {
            **server.DEFAULT_STRATEGY_CONFIG,
            "strategies": server.normalize_strategies(),
        }
        self.assertTrue(
            server.us_day_domestic_review_mode("US", "US 데이마켓", config)
        )
        self.assertFalse(
            server.us_day_domestic_review_mode("US", "US 프리마켓", config)
        )
        for strategy in config["strategies"]:
            if strategy["id"] == "us-day-domestic-review":
                strategy["enabled"] = False
        self.assertFalse(
            server.us_day_domestic_review_mode("US", "US 데이마켓", config)
        )

    def test_regular_market_close_window_uses_official_cached_calendar(self):
        original_cache = dict(server.CALENDAR_CACHE)
        server.CALENDAR_CACHE.update(
            {
                "US": {
                    "today": {
                        "regularMarket": {
                            "endTime": "2026-07-20T20:00:00+00:00",
                        }
                    }
                }
            }
        )
        try:
            remaining = server.market_minutes_to_close(
                "US", datetime(2026, 7, 20, 19, 56, tzinfo=timezone.utc)
            )
            self.assertAlmostEqual(remaining, 4)
        finally:
            server.CALENDAR_CACHE.clear()
            server.CALENDAR_CACHE.update(original_cache)

    @patch("server.run_multi_timeframe_study", return_value={"status": "completed"})
    def test_domestic_review_analyzes_only_kr_daily_weekly_monthly(self, study):
        result = server.run_domestic_day_review({})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(study.call_args.kwargs["markets"], ("KR",))
        self.assertEqual(study.call_args.kwargs["state_key"], "domesticDayReview")
        self.assertEqual(study.call_args.kwargs["study_type"], "US_DAY_DOMESTIC_REVIEW")

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
        self.assertIn("현재 승률: 33.3% (1승 / 3청산)", message)
        self.assertIn("오늘 손익금: +1,000원", message)
        self.assertNotIn("보유 포지션", message)
        self.assertNotIn("실시간 분석 요약", message)

    def test_time_exit_follow_up_observes_each_horizon_and_writes_verdict(self):
        closed = datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)
        study = server.build_post_exit_study(
            100.0,
            100.0,
            closed.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        orders = [
            {
                "id": "EXIT-TIME",
                "market": "KR",
                "symbol": "TEST",
                "side": "SELL",
                "status": "FILLED",
                "exitKind": "시간청산",
                "price": 100.0,
                "entryPrice": 100.0,
                "createdAt": closed.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "postExitStudy": study,
            }
        ]

        orders, changed = server.update_post_exit_studies_if_due(
            {}, orders, "KR", [{"symbol": "TEST", "lastPrice": 101.0}], closed + timedelta(minutes=5)
        )
        self.assertTrue(changed)
        self.assertEqual(study["horizons"]["5m"]["outcome"], "너무 이른 청산")
        self.assertEqual(study["horizons"]["10m"]["status"], "PENDING")

        orders, changed = server.update_post_exit_studies_if_due(
            {}, orders, "KR", [{"symbol": "TEST", "lastPrice": 99.0}], closed + timedelta(minutes=10)
        )
        self.assertTrue(changed)
        self.assertEqual(study["horizons"]["10m"]["outcome"], "손실 회피")

        orders, changed = server.update_post_exit_studies_if_due(
            {}, orders, "KR", [{"symbol": "TEST", "lastPrice": 102.0}], closed + timedelta(minutes=30)
        )
        self.assertTrue(changed)
        self.assertEqual(study["status"], "COMPLETE")
        self.assertEqual(study["observedCount"], 3)
        self.assertEqual(study["verdict"], "너무 이른 청산")
        self.assertEqual(study["latestValidHorizon"], "30m")
        self.assertIn("사후 5m:", "\n".join(server.post_exit_study_memo_lines(study)))
        self.assertEqual(server.post_exit_study_summary(orders)["completedCount"], 1)

    def test_trading_goal_roadmap_advances_and_keeps_final_rule_gate(self):
        stage_one_trades = [
            {"returnRate": 0.012 if index < 250 else -0.004}
            for index in range(500)
        ]
        stage_one = server.trading_goal_roadmap(stage_one_trades, 0)
        self.assertEqual(stage_one["stages"][0]["status"], "ACHIEVED")
        self.assertEqual(stage_one["stages"][1]["status"], "CURRENT")
        self.assertAlmostEqual(stage_one["winRate"], 0.5)
        self.assertAlmostEqual(stage_one["payoffRatio"], 3.0)

        final_trades = [
            {"returnRate": 0.01 if index < 520 else -0.005}
            for index in range(1000)
        ]
        final_with_violation = server.trading_goal_roadmap(final_trades, 1)
        self.assertEqual(final_with_violation["stages"][1]["status"], "ACHIEVED")
        self.assertEqual(final_with_violation["stages"][2]["status"], "CURRENT")
        self.assertFalse(final_with_violation["stages"][2]["achieved"])

        final_clear = server.trading_goal_roadmap(final_trades, 0)
        self.assertTrue(final_clear["stages"][2]["achieved"])

    def test_strategy_research_library_links_evidence_to_current_metrics(self):
        roadmap = server.trading_goal_roadmap(
            [{"returnRate": 0.01}, {"returnRate": -0.004}], 0
        )
        research = server.strategy_research_library(roadmap)
        self.assertEqual(research["phase"], "표본 확장")
        self.assertIn("승률 50.0%", research["snapshot"])
        self.assertEqual(len(research["principles"]), 4)
        self.assertTrue(
            all(item["sourceUrl"].startswith("https://") for item in research["principles"])
        )
        self.assertTrue(all(item.get("application") for item in research["principles"]))


if __name__ == "__main__":
    unittest.main()
