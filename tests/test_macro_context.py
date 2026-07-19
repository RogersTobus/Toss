import unittest

import server


class MacroContextTests(unittest.TestCase):
    def test_parses_and_classifies_official_rss(self):
        source = {"id": "FED", "market": "US", "name": "Federal Reserve"}
        xml = b"""<?xml version='1.0'?><rss><channel><item>
          <title>Inflation eased while growth accelerated</title>
          <link>https://example.test/release</link>
          <description>Official release</description>
          <pubDate>Fri, 17 Jul 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""
        rows = server.parse_rss_items(source, xml)
        self.assertEqual(len(rows), 1)
        classified = server.classify_macro_item(rows[0])
        self.assertEqual(classified["sourceTier"], "OFFICIAL")
        self.assertEqual(classified["signal"], "우호")
        self.assertFalse(classified["directTradingImpact"])

    def test_regime_uses_official_items_for_market(self):
        items = [
            {"market": "KR", "sourceTier": "OFFICIAL", "signalScore": -2},
            {"market": "KR", "sourceTier": "AGGREGATED", "signalScore": 2},
        ]
        regime = server.macro_regime(items, "KR")
        self.assertEqual(regime["regime"], "경계")
        self.assertEqual(regime["officialCount"], 1)

    def test_keyword_matching_does_not_treat_warned_as_war(self):
        item = server.classify_macro_item({"title": "WHO warned of an outbreak", "summary": "", "market": "GLOBAL"})
        self.assertEqual(item["signal"], "중립")
        self.assertNotIn("war", item["matchedFactors"])

    def test_korean_rate_hike_and_above_target_inflation_are_caution(self):
        item = server.classify_macro_item({
            "title": "통화정책방향",
            "summary": "기준금리를 2.75%로 상향 조정했으며 물가상승률은 목표수준을 상회한다.",
            "market": "KR",
        })
        self.assertEqual(item["signal"], "경계")
        self.assertIn("기준금리 인상", item["matchedFactors"])

    def test_trade_performance_groups_captured_macro_regime(self):
        orders = [
            {
                "id": "BUY",
                "market": "KR",
                "symbol": "TEST",
                "side": "BUY",
                "quantity": 1,
                "price": 100,
                "createdAt": "2026-07-17T09:10:00+0900",
                "entryScore": 82,
                "strategyIds": ["score-entry-80"],
                "macroContext": {"market": {"regime": "경계"}},
            },
            {
                "id": "SELL",
                "entryOrderId": "BUY",
                "market": "KR",
                "symbol": "TEST",
                "side": "SELL",
                "quantity": 1,
                "price": 101,
                "createdAt": "2026-07-17T09:13:00+0900",
            },
        ]
        analytics = server.trade_performance_analytics_from_orders(orders)
        self.assertEqual(analytics["byMacroRegime"][0]["key"], "KR · 경계")


if __name__ == "__main__":
    unittest.main()
