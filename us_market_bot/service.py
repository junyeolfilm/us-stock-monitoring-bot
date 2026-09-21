from __future__ import annotations

from datetime import datetime

from us_market_bot.config import Settings
from us_market_bot.database import MarketDatabase


class BriefingService:
    def __init__(self, settings: Settings, database: MarketDatabase) -> None:
        self.settings = settings
        self.database = database

    def generate(self, *, now: datetime | None = None) -> str:
        return self.generate_us(now=now)

    def generate_us(self, *, now: datetime | None = None) -> str:
        from us_market_bot.research import ResearchService

        return ResearchService(self.settings, self.database).morning(now)["body"]

    def generate_domestic(
        self,
        *,
        now: datetime | None = None,
        require_today: bool = False,
    ) -> tuple[str, str]:
        from us_market_bot.research import ResearchService

        edition = ResearchService(self.settings, self.database).close(now)
        return edition["trade_date"], edition["body"]
