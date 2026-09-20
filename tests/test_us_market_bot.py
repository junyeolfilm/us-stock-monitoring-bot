from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from us_market_bot.database import MarketDatabase, normalize_symbol
from us_market_bot.domestic import domestic_candidates
from us_market_bot.models import (
    ActiveStock,
    BenchmarkMove,
    MarketMover,
    MarketSnapshot,
    NewsItem,
)
from us_market_bot.report import build_report, split_report
from us_market_bot.scoring import rank_hot_stocks


def sample_snapshot() -> MarketSnapshot:
    now = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    return MarketSnapshot(
        collected_at=now,
        benchmarks=(
            BenchmarkMove("SPY", 600.0, 0.8),
            BenchmarkMove("QQQ", 520.0, 1.4),
            BenchmarkMove("SMH", 310.0, 2.5),
        ),
        gainers=(
            MarketMover("NVDA", 210.0, 5.2),
            MarketMover("TSLA", 450.0, 3.1),
        ),
        losers=(MarketMover("DAL", 42.0, -4.0),),
        actives=(
            ActiveStock("NVDA", 200_000_000, 900_000),
            ActiveStock("TSLA", 150_000_000, 700_000),
            ActiveStock("AAPL", 100_000_000, 500_000),
        ),
        news=(
            NewsItem(
                news_id="1",
                headline="AI chip demand lifts semiconductor shares",
                summary="",
                url="https://example.com/chips",
                source="Example",
                created_at=now,
                symbols=("NVDA", "AMD"),
            ),
            NewsItem(
                news_id="2",
                headline="Electric vehicle deliveries rise",
                summary="",
                url="https://example.com/ev",
                source="Example",
                created_at=now,
                symbols=("TSLA",),
            ),
        ),
    )


class ScoringTests(unittest.TestCase):
    def test_ranks_mover_with_volume_and_news_first(self) -> None:
        ranked = rank_hot_stocks(sample_snapshot(), ("AAPL", "NVDA"))
        self.assertEqual(ranked[0].symbol, "NVDA")
        self.assertGreater(ranked[0].heat_score, ranked[-1].heat_score)

    def test_maps_verified_sector_rules_to_domestic_candidates(self) -> None:
        snapshot = sample_snapshot()
        candidates = domestic_candidates(snapshot, rank_hot_stocks(snapshot, ("NVDA",)))
        sectors = {item.sector for item in candidates}
        self.assertIn("반도체·장비", sectors)
        semiconductor = next(item for item in candidates if item.sector == "반도체·장비")
        self.assertIn("삼성전자", semiconductor.names)


class ReportTests(unittest.TestCase):
    def test_report_contains_us_and_korean_sections(self) -> None:
        body = build_report(
            sample_snapshot(),
            ("NVDA", "AAPL"),
            local_now=datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
        )
        self.assertIn("미국 증시 아침 브리핑", body)
        self.assertIn("국내장 영향 관찰 후보", body)
        self.assertIn("NVDA", body)
        self.assertIn("투자 추천이 아닌", body)

    def test_long_report_is_split_under_discord_limit(self) -> None:
        chunks = split_report("\n".join(["테스트 문장"] * 700), limit=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))


class DatabaseTests(unittest.TestCase):
    def test_seeds_and_edits_watchlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = MarketDatabase(Path(directory) / "market.db")
            database.initialize()
            self.assertIn("SPY", database.watchlist())
            self.assertTrue(database.add_symbol("lly"))
            self.assertIn("LLY", database.watchlist())
            self.assertTrue(database.remove_symbol("LLY"))

    def test_rejects_invalid_symbol(self) -> None:
        with self.assertRaises(ValueError):
            normalize_symbol("삼성전자")


if __name__ == "__main__":
    unittest.main()

