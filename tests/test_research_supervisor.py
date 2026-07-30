import json
import tempfile
import unittest
from pathlib import Path

import research_supervisor


class ResearchSupervisorTests(unittest.TestCase):
    def test_records_signal_and_memory_when_child_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "research_worker_state.json"
            state_path.write_text(
                json.dumps({"status": "running", "phase": "intraday_replay"}),
                encoding="utf-8",
            )
            state = research_supervisor.record_child_exit(
                -9,
                611.4,
                state_path=state_path,
            )
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["workerExitSignal"], 9)
            self.assertEqual(state["peakObservedMemoryMb"], 611.4)
            self.assertIn("SIGKILL", state["lastError"])
            self.assertIn("611.4MB", state["lastError"])

    def test_preserves_successful_worker_result(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "research_worker_state.json"
            state_path.write_text(
                json.dumps({"status": "completed", "runCount": 2}),
                encoding="utf-8",
            )
            state = research_supervisor.record_child_exit(
                0,
                128.0,
                state_path=state_path,
            )
            self.assertEqual(state["status"], "completed")
            self.assertIsNone(state["workerExitSignal"])
            self.assertEqual(state["peakObservedMemoryMb"], 128.0)


if __name__ == "__main__":
    unittest.main()
