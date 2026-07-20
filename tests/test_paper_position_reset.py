import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


class PaperPositionResetTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.original_paths = (
            server.PAPER_PATH,
            server.JOURNAL_PATH,
            server.PAPER_RESET_ARCHIVE_DIR,
        )
        self.original_generation = server.OPEN_POSITION_RESET_GENERATION
        server.PAPER_PATH = root / "paper_state.json"
        server.JOURNAL_PATH = root / "journal_state.json"
        server.PAPER_RESET_ARCHIVE_DIR = root / "paper_reset_archives"
        server.OPEN_POSITION_RESET_GENERATION = "test-reset-generation"

    def tearDown(self):
        server.PAPER_PATH, server.JOURNAL_PATH, server.PAPER_RESET_ARCHIVE_DIR = self.original_paths
        server.OPEN_POSITION_RESET_GENERATION = self.original_generation
        self.directory.cleanup()

    @staticmethod
    def closed_orders():
        return [
            {
                "id": "CLOSED-BUY",
                "market": "KR",
                "symbol": "005930",
                "side": "BUY",
                "price": 100,
                "quantity": 1,
                "createdAt": "2026-07-17T09:10:00+0900",
            },
            {
                "id": "CLOSED-SELL",
                "entryOrderId": "CLOSED-BUY",
                "market": "KR",
                "symbol": "005930",
                "side": "SELL",
                "price": 101,
                "quantity": 1,
                "createdAt": "2026-07-17T09:11:00+0900",
            },
        ]

    @staticmethod
    def open_order(order_id="OPEN-BUY", symbol="000660"):
        return {
            "id": order_id,
            "market": "KR",
            "symbol": symbol,
            "side": "BUY",
            "price": 200,
            "quantity": 1,
            "createdAt": "2026-07-20T09:10:00+0900",
            "protectiveStopOrder": {"status": "WORKING"},
        }

    def test_archives_only_open_positions_and_preserves_closed_history(self):
        state = server.new_paper_state()
        state["orders"] = [*self.closed_orders(), self.open_order()]
        server.save_paper_state(state)
        server.save_journal_state(
            {
                "notes": {
                    "CLOSED-BUY": {"memo": "preserve"},
                    "OPEN-BUY": {"memo": "retire"},
                },
                "reviews": {},
            }
        )

        result = server.retire_open_paper_positions_once()

        self.assertTrue(result["applied"])
        self.assertEqual(result["retiredPositionCount"], 1)
        saved = server.load_paper_state()
        self.assertEqual([item["id"] for item in saved["orders"]], ["CLOSED-BUY", "CLOSED-SELL"])
        self.assertEqual(saved["boundedRiskStartedAt"], saved["openPositionResetAt"])
        self.assertEqual(server.load_journal_state()["notes"], {"CLOSED-BUY": {"memo": "preserve"}})
        archives = list(server.PAPER_RESET_ARCHIVE_DIR.glob("*.json"))
        self.assertEqual(len(archives), 1)

    def test_generation_marker_does_not_remove_positions_opened_after_reset(self):
        state = server.new_paper_state()
        state["orders"] = [self.open_order()]
        server.save_paper_state(state)
        server.retire_open_paper_positions_once()

        state = server.load_paper_state()
        state["orders"].append(self.open_order("NEW-BUY", "035420"))
        server.save_paper_state(state)

        result = server.retire_open_paper_positions_once()

        self.assertFalse(result["applied"])
        self.assertEqual(list(server.open_paper_positions(server.load_paper_orders())), ["035420"])


if __name__ == "__main__":
    unittest.main()
