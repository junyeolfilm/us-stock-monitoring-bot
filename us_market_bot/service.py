from __future__ import annotations

from datetime import datetime

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase
from us_market_bot.providers.alpaca import AlpacaMarketData
from us_market_bot.report import build_report


class BriefingService:
    def __init__(self, settings: Settings, database: MarketDatabase) -> None:
        self.settings = settings
        self.database = database

    def generate(self, *, now: datetime | None = None) -> str:
        local_now = now or datetime.now(self.settings.timezone)
        client = AlpacaMarketData(
            self.settings.alpaca_key_id,
            self.settings.alpaca_secret_key,
            feed=self.settings.alpaca_feed,
        )
        snapshot = client.collect(self.database.watchlist())
        body = build_report(snapshot, self.database.watchlist(), local_now=local_now)
        self.database.save_report(local_now.date().isoformat(), body)
        return body

