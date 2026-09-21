from __future__ import annotations

from datetime import datetime

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase
from us_market_bot.providers.alpaca import AlpacaMarketData
from us_market_bot.providers.naver import NaverDomesticMarketData
from us_market_bot.report import build_domestic_report, build_report


class BriefingService:
    def __init__(self, settings: Settings, database: MarketDatabase) -> None:
        self.settings = settings
        self.database = database

    def generate(self, *, now: datetime | None = None) -> str:
        return self.generate_us(now=now)

    def generate_us(self, *, now: datetime | None = None) -> str:
        local_now = now or datetime.now(self.settings.timezone)
        client = AlpacaMarketData(
            self.settings.alpaca_key_id,
            self.settings.alpaca_secret_key,
            feed=self.settings.alpaca_feed,
        )
        snapshot = client.collect(self.database.watchlist())
        body = build_report(
            snapshot,
            self.database.watchlist(),
            local_now=local_now,
            previous_domestic=self.database.latest_domestic_snapshot(),
        )
        self.database.save_report(local_now.date().isoformat(), body)
        return body

    def generate_domestic(
        self,
        *,
        now: datetime | None = None,
        require_today: bool = False,
    ) -> tuple[str, str]:
        local_now = now or datetime.now(self.settings.timezone)
        snapshot = NaverDomesticMarketData().collect()
        if require_today and snapshot.market_date != local_now.date().isoformat():
            raise ValueError(
                f"오늘({local_now:%Y-%m-%d}) 국내장 종가가 아직 확인되지 않았습니다. "
                f"최근 거래일은 {snapshot.market_date}입니다."
            )
        body = build_domestic_report(snapshot, local_now=local_now)
        self.database.save_domestic_report(snapshot, body)
        return snapshot.market_date, body
