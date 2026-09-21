from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_WATCHLIST = (
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "SMH",
    "XLE",
    "XLF",
    "XLV",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "AVGO",
    "GOOGL",
    "META",
    "AMZN",
    "TSLA",
)


def _integer(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_channel_id: int
    discord_bot_name: str
    alpaca_key_id: str
    alpaca_secret_key: str
    alpaca_feed: str
    database_path: Path
    report_hour: int
    report_minute: int
    domestic_report_hour: int
    domestic_report_minute: int
    timezone: ZoneInfo

    @classmethod
    def from_env(cls) -> "Settings":
        timezone_name = os.environ.get("US_MARKET_TIMEZONE", "Asia/Seoul")
        return cls(
            discord_token=os.environ.get("DISCORD_BOT_TOKEN", "").strip(),
            discord_channel_id=_integer("DISCORD_CHANNEL_ID", 0),
            discord_bot_name=os.environ.get(
                "DISCORD_BOT_NAME", "미국 주식 동향 봇"
            ).strip(),
            alpaca_key_id=os.environ.get("ALPACA_API_KEY_ID", "").strip(),
            alpaca_secret_key=os.environ.get("ALPACA_API_SECRET_KEY", "").strip(),
            alpaca_feed=os.environ.get("ALPACA_DATA_FEED", "iex").strip() or "iex",
            database_path=Path(os.environ.get("US_MARKET_DB", "data/us_market.db")),
            report_hour=max(0, min(23, _integer("US_MARKET_REPORT_HOUR", 8))),
            report_minute=max(0, min(59, _integer("US_MARKET_REPORT_MINUTE", 0))),
            domestic_report_hour=max(0, min(23, _integer("KR_MARKET_REPORT_HOUR", 15))),
            domestic_report_minute=max(
                0, min(59, _integer("KR_MARKET_REPORT_MINUTE", 35))
            ),
            timezone=ZoneInfo(timezone_name),
        )

    def missing(self, *, include_discord: bool = True) -> tuple[str, ...]:
        values = {
            "ALPACA_API_KEY_ID": self.alpaca_key_id,
            "ALPACA_API_SECRET_KEY": self.alpaca_secret_key,
        }
        if include_discord:
            values.update(
                {
                    "DISCORD_BOT_TOKEN": self.discord_token,
                    "DISCORD_CHANNEL_ID": str(self.discord_channel_id or ""),
                }
            )
        return tuple(name for name, value in values.items() if not value)
