from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class BenchmarkMove:
    symbol: str
    price: float
    change_percent: float


@dataclass(frozen=True)
class MarketMover:
    symbol: str
    price: float
    change_percent: float


@dataclass(frozen=True)
class ActiveStock:
    symbol: str
    volume: int
    trade_count: int = 0


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    headline: str
    summary: str
    url: str
    source: str
    created_at: datetime
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class MarketSnapshot:
    collected_at: datetime
    benchmarks: tuple[BenchmarkMove, ...]
    gainers: tuple[MarketMover, ...]
    losers: tuple[MarketMover, ...]
    actives: tuple[ActiveStock, ...]
    news: tuple[NewsItem, ...]


@dataclass(frozen=True)
class HotStock:
    symbol: str
    price: float | None
    change_percent: float | None
    heat_score: float
    active_rank: int | None
    news_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DomesticCandidate:
    sector: str
    names: tuple[str, ...]
    outlook: str
    confidence: str
    triggers: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class DomesticIndexMove:
    symbol: str
    name: str
    price: float
    change_percent: float
    market_status: str


@dataclass(frozen=True)
class DomesticStockMove:
    code: str
    name: str
    market: str
    price: float
    change_percent: float
    volume: int
    trading_value: int
    url: str


@dataclass(frozen=True)
class DomesticSnapshot:
    market_date: str
    collected_at: datetime
    indices: tuple[DomesticIndexMove, ...]
    gainers: tuple[DomesticStockMove, ...]
    losers: tuple[DomesticStockMove, ...]
    value_leaders: tuple[DomesticStockMove, ...]
