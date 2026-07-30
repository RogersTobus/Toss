import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import research_worker
import server


class ResearchWorkerTests(unittest.TestCase):
    def test_regular_market_blocks_research(self):
        self.assertEqual(
            research_worker.select_research_markets([("KR", "KR 정규장")]),
            (),
        )
        self.assertEqual(
            research_worker.select_research_markets([("US", "US 정규장")]),
            (),
        )

    def test_us_day_session_reviews_kr_only(self):
        self.assertEqual(
            research_worker.select_research_markets([("US", "US 데이마켓")]),
            ("KR",),
        )
        self.assertEqual(research_worker.select_research_markets([]), ("KR", "US"))

    def test_cycle_runs_replays_sequentially_and_accumulates_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "research_worker_state.json"
            original_learning_path = server.LEARNING_PATH
            patches = (
                mock.patch.object(research_worker, "STATE_PATH", state_path),
                mock.patch.object(server, "load_env", return_value={}),
                mock.patch.object(server, "active_market_sessions", return_value=[]),
                mock.patch.object(
                    server,
                    "run_intraday_backtest_cycle",
                    return_value={
                        "status": "completed",
                        "completedAt": "2026-07-30T18:00:00+0900",
                        "tradeCount": 0,
                        "analyzed": [{"symbol": "A"}, {"symbol": "B"}],
                        "errors": [],
                    },
                ),
                mock.patch.object(
                    server,
                    "run_off_market_study",
                    return_value={
                        "status": "completed",
                        "completedAt": "2026-07-30T18:01:00+0900",
                        "analyzedSymbolCount": 16,
                        "summary": {"patternObservationCount": 240},
                        "errors": [],
                    },
                ),
            )
            with patches[0], patches[1], patches[2], patches[3] as replay, patches[4] as study:
                first = research_worker.run_research_cycle()
                second = research_worker.run_research_cycle()

            self.assertEqual(first["status"], "completed")
            self.assertEqual(second["runCount"], 2)
            self.assertEqual(second["totalAnalyzedSymbolCount"], 32)
            self.assertEqual(second["totalIntradayAnalyzedSymbolCount"], 4)
            self.assertEqual(replay.call_count, 2)
            self.assertEqual(study.call_count, 2)
            self.assertEqual(replay.call_args.args[1], ("KR", "US"))
            self.assertEqual(study.call_args.args[1], ("KR", "US"))
            self.assertEqual(server.LEARNING_PATH, original_learning_path)

    def test_health_snapshot_marks_stale_running_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "research_worker_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "heartbeatAt": "2020-01-01T00:00:00+0900",
                        "runCount": 3,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(server, "RESEARCH_WORKER_STATE_PATH", state_path):
                snapshot = server.research_worker_snapshot()
            self.assertEqual(snapshot["status"], "stale")
            self.assertFalse(snapshot["healthy"])
            self.assertIn("하트비트", snapshot["lastError"])

    def test_main_process_does_not_start_intraday_research_thread(self):
        self.assertFalse(server.INTRADAY_BACKTEST_AUTO_ENABLED)
        self.assertFalse(server.OFF_MARKET_STUDY_AUTO_ENABLED)
        self.assertFalse(server.DOMESTIC_DAY_REVIEW_AUTO_ENABLED)

    def test_dashboard_prefers_separate_research_state(self):
        with tempfile.TemporaryDirectory() as directory:
            research_path = Path(directory) / "research_learning_state.json"
            research = server.default_learning_state()
            research["offlineStudy"] = {
                "status": "completed",
                "analyzedSymbolCount": 16,
            }
            research["intradayBacktest"] = {
                "status": "completed",
                "tradeCount": 7,
            }
            research_path.write_text(json.dumps(research), encoding="utf-8")
            live = server.default_learning_state()
            live["offlineStudy"] = {"status": "running", "analyzedSymbolCount": 1}
            with mock.patch.object(server, "RESEARCH_LEARNING_PATH", research_path):
                payload = server.learning_brain_payload(live)
            self.assertEqual(payload["offlineStudy"]["analyzedSymbolCount"], 16)
            self.assertEqual(payload["intradayBacktest"]["tradeCount"], 7)


if __name__ == "__main__":
    unittest.main()
