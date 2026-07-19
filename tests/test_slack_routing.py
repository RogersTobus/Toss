import unittest
from unittest.mock import patch

import server


class SlackRoutingTests(unittest.TestCase):
    def test_log_webhook_unifies_every_message_type(self):
        env = {
            "SLACK_ALERT_WEBHOOK_URL": "https://alert.invalid",
            "SLACK_REPORT_WEBHOOK_URL": "https://report.invalid",
            "SLACK_LOG_WEBHOOK_URL": "https://log.invalid",
        }
        for channel in ("alert", "report", "log"):
            self.assertEqual(server.slack_route(env, channel), ("log", "https://log.invalid"))
            self.assertTrue(server.slack_enabled(env, channel))
        status = server.slack_status(env)
        self.assertTrue(all(item["unified"] for item in status.values()))

    def test_legacy_webhooks_remain_fallback_without_log_webhook(self):
        env = {"SLACK_ALERT_WEBHOOK_URL": "https://alert.invalid"}
        self.assertEqual(server.slack_route(env, "alert"), ("alert", "https://alert.invalid"))
        self.assertFalse(server.slack_enabled(env, "report"))

    def test_log_disable_switch_disables_all_unified_routes(self):
        env = {"SLACK_LOG_WEBHOOK_URL": "https://log.invalid", "SLACK_LOG_ENABLED": "false"}
        self.assertFalse(server.slack_enabled(env, "alert"))
        self.assertFalse(server.slack_enabled(env, "report"))

    @patch("server.post_json")
    @patch("server.load_env", return_value={"SLACK_LOG_WEBHOOK_URL": "https://log.invalid"})
    def test_send_uses_log_webhook(self, _env, post_json):
        server.send_slack("alert", "problem")
        post_json.assert_called_once_with("https://log.invalid", {"text": "problem"})

