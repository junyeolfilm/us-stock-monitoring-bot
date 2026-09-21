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
    DomesticIndexMove,
    DomesticSnapshot,
    DomesticStockMove,
    MarketMover,
    MarketSnapshot,
    NewsItem,
)
from us_market_bot.providers.naver import NaverDomesticMarketData
from us_market_bot.report import build_domestic_report, build_report, split_report
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


def sample_domestic_snapshot() -> DomesticSnapshot:
    now = datetime(2026, 9, 19, 7, 0, tzinfo=timezone.utc)
    samsung = DomesticStockMove(
        code="005930",
        name="삼성전자",
        market="KOSPI",
        price=80_000,
        change_percent=2.5,
        volume=20_000_000,
        trading_value=1_600_000_000_000,
        url="https://stock.naver.com/domestic/stock/005930",
    )
    return DomesticSnapshot(
        market_date="2026-09-19",
        collected_at=now,
        indices=(
            DomesticIndexMove("KOSPI", "코스피", 3_200.0, 1.2, "CLOSE"),
            DomesticIndexMove("KOSDAQ", "코스닥", 900.0, 0.4, "CLOSE"),
        ),
        gainers=(samsung,),
        losers=(
            DomesticStockMove(
                code="000000",
                name="테스트하락",
                market="KOSDAQ",
                price=10_000,
                change_percent=-5.0,
                volume=1_000,
                trading_value=100_000_000,
                url="https://example.com/down",
            ),
        ),
        value_leaders=(samsung,),
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

    def test_us_report_uses_previous_domestic_close_for_forecast(self) -> None:
        body = build_report(
            sample_snapshot(),
            ("NVDA",),
            local_now=datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
            previous_domestic=sample_domestic_snapshot(),
        )

        self.assertIn("오늘 국내장 예상 시나리오", body)
        self.assertIn("전일 국내장(2026-09-19)", body)
        self.assertIn("연속 관찰 후보: 삼성전자", body)
        self.assertIn("반대 조건", body)

    def test_domestic_close_report_contains_indices_and_value_leaders(self) -> None:
        body = build_domestic_report(
            sample_domestic_snapshot(),
            local_now=datetime(2026, 9, 19, 16, 0, tzinfo=timezone.utc),
        )

        self.assertIn("국내 증시 마감 브리핑", body)
        self.assertIn("코스피", body)
        self.assertIn("거래대금 상위", body)
        self.assertIn("삼성전자", body)

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

    def test_round_trips_domestic_report_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = MarketDatabase(Path(directory) / "market.db")
            database.initialize()
            original = sample_domestic_snapshot()

            database.save_domestic_report(original, "국내장 보고서")
            restored = database.latest_domestic_snapshot()

            self.assertEqual(database.latest_domestic_report(), "국내장 보고서")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.market_date, original.market_date)
            self.assertEqual(restored.indices, original.indices)
            self.assertEqual(restored.value_leaders, original.value_leaders)


class NaverProviderTests(unittest.TestCase):
    def test_collects_indices_and_combines_market_rankings(self) -> None:
        class Response:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, url, *, params, timeout):
                if "/index/" in url:
                    symbol = "KOSDAQ" if "KOSDAQ" in url else "KOSPI"
                    return Response(
                        {
                            "itemCode": symbol,
                            "stockName": "코스닥" if symbol == "KOSDAQ" else "코스피",
                            "closePrice": "900.50",
                            "fluctuationsRatio": "1.20",
                            "marketStatus": "CLOSE",
                            "localTradedAt": "2026-09-19T16:15:00+09:00",
                        }
                    )
                change = -4.5 if "/down/" in url else 5.5
                value = 900_000_000_000 if "KOSPI" in url else 800_000_000_000
                return Response(
                    {
                        "stocks": [
                            {
                                "itemCode": "005930" if "KOSPI" in url else "247540",
                                "stockName": "삼성전자" if "KOSPI" in url else "에코프로비엠",
                                "closePriceRaw": "80000",
                                "fluctuationsRatio": str(change),
                                "accumulatedTradingVolumeRaw": "1000000",
                                "accumulatedTradingValueRaw": str(value),
                                "newPcUrl": "https://example.com/stock",
                            }
                        ]
                    }
                )

        snapshot = NaverDomesticMarketData(session=Session()).collect(list_size=5)

        self.assertEqual(snapshot.market_date, "2026-09-19")
        self.assertEqual(len(snapshot.indices), 2)
        self.assertEqual(snapshot.gainers[0].change_percent, 5.5)
        self.assertEqual(snapshot.losers[0].change_percent, -4.5)
        self.assertEqual(snapshot.value_leaders[0].name, "삼성전자")


if __name__ == "__main__":
    unittest.main()
