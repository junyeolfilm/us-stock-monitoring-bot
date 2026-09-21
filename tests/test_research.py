from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from us_market_bot.calendar import session, latest_closed, prior_sessions
from us_market_bot.database import MarketDatabase
from us_market_bot.journal import Journal
from us_market_bot.prices import rounded, atr, quantity, make_card, review_card
from us_market_bot.profile import InvestmentProfile
from us_market_bot.research import review_hypothesis, hypotheses
from us_market_bot.research import ResearchService
from us_market_bot.config import Settings
from us_market_bot.research_data import KST
from us_market_bot.research_data import aggregate_regular, ResearchData
from us_market_bot.providers.alpaca import AlpacaMarketData, MarketDataError
from us_market_bot.report import split_report

UTC = timezone.utc


class CalendarTests(unittest.TestCase):
    def test_holidays_dst_early_close(self):
        self.assertIsNone(session("XNYS", "2026-07-03"))
        self.assertIsNone(session("XKRX", "2026-09-25"))
        self.assertEqual(session("XNYS", "2026-03-06").open.hour, 14)
        self.assertEqual(session("XNYS", "2026-03-09").open.hour, 13)
        self.assertEqual(session("XNYS", "2026-11-27").close.hour, 18)
        self.assertEqual(len(prior_sessions("XNYS", "2026-09-18", 26)), 26)

    def test_latest_requires_completed_session_and_delay(self):
        self.assertEqual(
            latest_closed("XNYS", datetime(2026, 9, 21, 19, tzinfo=UTC)).date,
            "2026-09-18",
        )
        self.assertEqual(
            latest_closed("XNYS", datetime(2026, 9, 21, 20, 10, tzinfo=UTC)).date,
            "2026-09-18",
        )


class EvidenceTests(unittest.TestCase):
    def test_regular_aggregation_excludes_extended_and_future(self):
        s = session("XNYS", "2026-11-27")
        raw = []
        for i in range(-1, 44):
            t = s.open + timedelta(minutes=5 * i)
            raw.append(dict(t=t.isoformat(), o=100, h=105, l=99, c=102, v=10))
        result = aggregate_regular(raw, [s])[0]
        self.assertEqual(result["bar_count"], 42)
        self.assertEqual(result["volume"], 420)
        self.assertTrue(result["complete"])
        raw.pop(10)
        self.assertFalse(aggregate_regular(raw, [s])[0]["complete"])

    def test_news_after_cutoff_or_edited_later_excluded(self):
        data = ResearchData.__new__(ResearchData)
        data.client = Mock()
        data.warnings = []
        data.client._get.return_value = {
            "news": [
                dict(
                    created_at="2026-09-18T18:00:00Z",
                    updated_at="2026-09-19T01:00:00Z",
                    headline="future edit",
                ),
                dict(created_at="2026-09-18T18:00:00Z", headline="on time"),
            ]
        }
        result = data.news(
            [], datetime(2026, 9, 18, tzinfo=UTC), datetime(2026, 9, 18, 20, tzinfo=UTC)
        )
        self.assertEqual([n["title"] for n in result], ["on time"])


class JournalTests(unittest.TestCase):
    def test_immutable_original_delivery_race_and_append_only_state(self):
        with tempfile.TemporaryDirectory() as folder:
            db = MarketDatabase(Path(folder) / "test.db")
            db.initialize()
            j = Journal(db.path)
            now = datetime.now(UTC)
            original = j.save(
                "morning", "2026-09-21", now, "original", {"hypotheses": [1]}
            )
            j.save("morning", "2026-09-21", now, "hindsight", {"hypotheses": [2]})
            self.assertEqual(j.get(original["id"])["body"], "original")
            with j.connect() as c:
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("UPDATE research_editions SET body='bad'")
            with ThreadPoolExecutor(4) as pool:
                results = list(pool.map(lambda _: j.claim(original["id"], 0), range(8)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(j.unresolved(), 1)
            j.delivered(original["id"], 0, 123)
            self.assertEqual(j.unresolved(), 0)
            j.event(original["id"], "card1", "무효화", "공시")
            self.assertEqual(j.subject_status("card1"), "무효화")


class PriceTests(unittest.TestCase):
    def test_real_levels_and_risk_reward_are_calculated_not_percentage_targets(self):
        sessions = prior_sessions("XNYS", "2026-09-18", 26)
        bars = [
            dict(
                date=s.date,
                asof=s.close.isoformat(),
                open=100,
                close=100,
                low=99 if i == 20 else 98,
                high=110 if i % 5 == 0 else 105,
                volume=1_000_000,
                complete=True,
            )
            for i, s in enumerate(sessions)
        ]
        card, reason = make_card(
            "AAPL",
            "Apple",
            "tech",
            bars,
            market="US",
            session_date="2026-09-18",
            valid_session=session("XNYS", "2026-09-21"),
            feed="sip",
            profile=InvestmentProfile(),
            now=datetime(2026, 9, 21, 0, tzinfo=UTC),
        )
        self.assertIsNotNone(card, reason)
        self.assertEqual(card["target1"], 105)
        self.assertEqual(card["target2"], 110)
        self.assertLessEqual(card["entry"], card["reference"])
        self.assertEqual(
            card["rr1"],
            (card["target1"] - card["entry"]) / (card["entry"] - card["stop_trigger"]),
        )
        self.assertIsNone(card["quantity"])
        self.assertIsNone(card["stop_order_price"])
        self.assertIn("일반 매도 지정가로 설정하지", card["stop_method"])
        bars[-1]["date"] = "2026-09-17"
        self.assertIsNone(
            make_card(
                "AAPL",
                "Apple",
                "tech",
                bars,
                market="US",
                session_date="2026-09-18",
                valid_session=session("XNYS", "2026-09-21"),
                feed="sip",
                profile=InvestmentProfile(),
                now=datetime.now(UTC),
            )[0]
        )

    def test_ticks_and_cost_aware_risk_quantity(self):
        self.assertEqual(rounded(1999.9, "KOSPI", "2026-09-21"), 1999)
        self.assertEqual(rounded(2001, "KOSDAQ", "2026-09-21", up=True), 2005)
        self.assertEqual(rounded(100.019, "US", "2026-09-21"), 100.01)
        p = InvestmentProfile(
            available_cash=10000,
            per_symbol_limit=5000,
            loss_budget=100,
            costs=dict(
                buy_rate=0.001,
                sell_rate=0.001,
                slippage_per_share=0.1,
                fx_cost_per_share=0,
            ),
        )
        self.assertEqual(quantity(100, 95, p, cash_left=10000, sector_left=5000), 18)
        self.assertEqual(quantity(100, 95, p, cash_left=500, sector_left=5000), 4)

    def test_missing_settings_never_generate_personal_quantity(self):
        self.assertFalse(InvestmentProfile().can_size(datetime.now(UTC), "USD"))

    def test_atr_uses_gaps(self):
        bars = [dict(high=101, low=99, close=100) for _ in range(14)] + [
            dict(high=112, low=109, close=111)
        ]
        self.assertAlmostEqual(atr(bars), (13 * 2 + 12) / 14)

    def test_insufficient_history_and_iex_withheld(self):
        kwargs = dict(
            market="US",
            session_date="2026-09-18",
            valid_session=session("XNYS", "2026-09-21"),
            profile=InvestmentProfile(),
            now=datetime.now(UTC),
        )
        self.assertIsNone(
            make_card("AAPL", "Apple", "tech", [], feed="sip", **kwargs)[0]
        )
        self.assertIsNone(
            make_card("AAPL", "Apple", "tech", [], feed="iex", **kwargs)[0]
        )

    def test_both_stop_target_intraday_not_a_fill_or_win(self):
        card = dict(
            valid_from="2026-09-21T13:30:00+00:00",
            valid_until="2026-09-21T20:00:00+00:00",
            entry=100,
            entry_low=99,
            stop_trigger=95,
            target1=110,
        )
        bar = dict(
            open=100, low=90, high=115, asof="2026-09-21T20:00:00+00:00", complete=True
        )
        r = review_card(card, bar, datetime(2026, 9, 22, tzinfo=UTC))
        self.assertEqual(r["status"], "판단 유보")
        self.assertIn("순서 불명", r["reason"])
        self.assertIn("실제 체결은 확인되지", r["reason"])
        self.assertEqual(
            review_card(card, None, datetime(2026, 9, 21, 10, tzinfo=UTC))["decision"],
            "유지",
        )


class ReviewTests(unittest.TestCase):
    def test_all_hypotheses_can_be_reviewed_without_rewriting(self):
        hs = hypotheses({"SPY": {"change": 1}, "QQQ": {"change": 2}}, "2026-09-21")
        indices = {"KOSPI": {"change": -1}, "KOSDAQ": {"change": 1}}
        result = [review_hypothesis(h, indices) for h in hs]
        self.assertEqual([r["status"] for r in result], ["불일치", "관찰과 부합"])
        self.assertEqual(review_hypothesis(hs[0], {})["status"], "판단 유보")

    def test_order_endpoints_cannot_be_called(self):
        c = AlpacaMarketData("fake", "fake", session=Mock())
        with self.assertRaises(MarketDataError):
            c._get("/v2/orders", {})
        c.session.get.assert_not_called()

    def test_long_external_line_fits_discord(self):
        self.assertTrue(all(len(c) <= 1900 for c in split_report("x" * 5000)))


class WorkflowTests(unittest.TestCase):
    def make_service(self, folder):
        database = MarketDatabase(Path(folder) / "state.db")
        data = Mock()
        data.warnings = []
        data.regular_history.return_value = {}
        data.news.return_value = []
        data.official_events.return_value = []
        data.domestic.return_value = {
            "indices": {
                "KOSPI": {
                    "name": "코스피",
                    "close": 100,
                    "change": 1,
                    "at": "2026-09-21T15:30:00+09:00",
                    "source": "https://example.com",
                    "session": "regular",
                    "delay": "unknown",
                }
            },
            "stocks": [],
            "asof": "2026-09-21T15:35:00+09:00",
            "date": "2026-09-21",
        }
        return ResearchService(Settings.from_env(), database, data), data

    def test_close_links_original_all_hypotheses_and_cannot_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            svc, data = self.make_service(folder)
            now = datetime(2026, 9, 21, 8, tzinfo=KST)
            original = svc.journal.save(
                "morning",
                "2026-09-21",
                now,
                "saved before open",
                {
                    "hypotheses": hypotheses(
                        {"SPY": {"change": 1}, "QQQ": {"change": 2}}, "2026-09-21"
                    ),
                    "cards": [],
                },
            )
            close = svc.close(datetime(2026, 9, 21, 15, 35, tzinfo=KST))
            self.assertEqual(close["payload"]["morning_id"], original["id"])
            self.assertEqual(len(close["payload"]["hypothesis_reviews"]), 2)
            self.assertEqual(
                close["payload"]["hypothesis_reviews"][1]["status"], "판단 유보"
            )
            self.assertEqual(
                svc.journal.get(original["id"])["body"], "saved before open"
            )
            again = svc.close(datetime(2026, 9, 21, 16, tzinfo=KST))
            self.assertEqual(close["body"], again["body"])
            data.domestic.assert_called_once()

    def test_no_post_open_morning_no_holiday_no_fake_historical_review(self):
        with tempfile.TemporaryDirectory() as folder:
            svc, data = self.make_service(folder)
            with self.assertRaises(ValueError):
                svc.morning(datetime(2026, 9, 21, 10, tzinfo=KST))
            with self.assertRaises(ValueError):
                svc.morning(datetime(2026, 9, 25, 8, tzinfo=KST))
            with self.assertRaises(ValueError):
                svc.close(datetime(2026, 9, 25, 16, tzinfo=KST))
            preview = svc.morning(datetime(2026, 9, 21, 16, tzinfo=KST), preview=True)
            close = svc.close(datetime(2026, 9, 21, 16, tzinfo=KST), preview=True)
            self.assertIsNone(close["payload"]["morning_id"])
            self.assertTrue(preview["id"].startswith("preview:"))

    def test_us_holiday_does_not_reuse_quotes_or_create_new_scenarios(self):
        with tempfile.TemporaryDirectory() as folder:
            svc, data = self.make_service(folder)
            svc.journal.save(
                "morning",
                "2026-09-07",
                datetime(2026, 9, 7, 8, tzinfo=KST),
                "previous",
                {
                    "us_session": "2026-09-04",
                    "moves": {"SPY": {"change": 1}},
                    "hypotheses": [],
                    "cards": [],
                },
            )
            edition = svc.morning(datetime(2026, 9, 8, 8, tzinfo=KST))
            self.assertTrue(edition["payload"]["repeated_us"])
            self.assertEqual(edition["payload"]["hypotheses"], [])
            self.assertEqual(edition["payload"]["cards"], [])
            data.regular_history.assert_not_called()

    def test_market_data_failure_produces_partial_report_without_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            svc, data = self.make_service(folder)
            data.regular_history.side_effect = MarketDataError("offline")
            edition = svc.morning(datetime(2026, 9, 21, 8, tzinfo=KST))
            self.assertIn("오늘은 신규 매수 제안 없음", edition["body"])
            self.assertTrue(edition["payload"]["warnings"])


if __name__ == "__main__":
    unittest.main()
