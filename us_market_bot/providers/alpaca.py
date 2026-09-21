from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from us_market_bot.models import (
    ActiveStock,
    BenchmarkMove,
    MarketMover,
    MarketSnapshot,
    NewsItem,
)


class MarketDataError(RuntimeError):
    pass


class AlpacaMarketData:
    base_url = "https://data.alpaca.markets"
    benchmark_symbols = ("SPY", "QQQ", "DIA", "IWM", "SMH", "XLE", "XLF", "XLV")

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        feed: str = "iex",
        session: requests.Session | None = None,
    ) -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca API 키가 필요합니다.")
        self.feed = feed
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": key_id,
                "APCA-API-SECRET-KEY": secret_key,
                "User-Agent": "us-stock-monitoring-bot/0.1",
                "Accept": "application/json",
            }
        )

    def collect(self, watchlist: tuple[str, ...]) -> MarketSnapshot:
        gainers, losers = self.movers(top=20)
        actives = self.most_actives(top=50)
        dynamic_symbols = {
            *(item.symbol for item in gainers),
            *(item.symbol for item in losers),
            *(item.symbol for item in actives[:30]),
            *watchlist,
        }
        return MarketSnapshot(
            collected_at=datetime.now(timezone.utc),
            benchmarks=self.benchmarks(),
            gainers=gainers,
            losers=losers,
            actives=actives,
            news=self.news(tuple(sorted(dynamic_symbols)), hours=30, limit=50),
        )

    def movers(
        self, *, top: int = 20
    ) -> tuple[tuple[MarketMover, ...], tuple[MarketMover, ...]]:
        payload = self._get(
            "/v1beta1/screener/stocks/movers", {"top": max(1, min(top, 50))}
        )
        return (
            tuple(self._parse_mover(item) for item in payload.get("gainers", [])),
            tuple(self._parse_mover(item) for item in payload.get("losers", [])),
        )

    def most_actives(self, *, top: int = 50) -> tuple[ActiveStock, ...]:
        payload = self._get(
            "/v1beta1/screener/stocks/most-actives",
            {"by": "volume", "top": max(1, min(top, 100))},
        )
        values = payload.get("most_actives", payload.get("mostActives", []))
        return tuple(
            ActiveStock(
                symbol=str(item.get("symbol", "")).upper(),
                volume=_integer(item.get("volume")),
                trade_count=_integer(item.get("trade_count", item.get("trades"))),
            )
            for item in values
            if item.get("symbol")
        )

    def benchmarks(self) -> tuple[BenchmarkMove, ...]:
        payload = self._get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(self.benchmark_symbols), "feed": self.feed},
        )
        snapshots = payload.get("snapshots", payload)
        output: list[BenchmarkMove] = []
        for symbol in self.benchmark_symbols:
            item = snapshots.get(symbol, {})
            daily = item.get("dailyBar", {})
            previous = item.get("prevDailyBar", {})
            close = _number(daily.get("c"))
            previous_close = _number(previous.get("c"))
            if not close or not previous_close:
                continue
            change = ((close / previous_close) - 1.0) * 100
            output.append(
                BenchmarkMove(symbol=symbol, price=close, change_percent=change)
            )
        return tuple(output)

    def news(
        self,
        symbols: tuple[str, ...],
        *,
        hours: int = 30,
        limit: int = 50,
    ) -> tuple[NewsItem, ...]:
        start = datetime.now(timezone.utc) - timedelta(hours=hours)
        params: dict[str, Any] = {
            "start": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "sort": "desc",
            "limit": max(1, min(limit, 50)),
            "exclude_contentless": "true",
        }
        if symbols:
            params["symbols"] = ",".join(symbols[:100])
        payload = self._get("/v1beta1/news", params)
        values = payload.get("news", [])
        return tuple(self._parse_news(item) for item in values if item.get("headline"))

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "/v2/stocks/bars",
            "/v2/stocks/snapshots",
            "/v1beta1/news",
            "/v1beta1/screener/stocks/movers",
            "/v1beta1/screener/stocks/most-actives",
        }
        if path not in allowed or self.base_url != "https://data.alpaca.markets":
            raise MarketDataError("브리핑 전용 읽기 허용목록 밖의 API 요청 차단")
        try:
            response = self.session.get(
                f"{self.base_url}{path}", params=params, timeout=(5, 20)
            )
        except requests.RequestException as exc:
            raise MarketDataError(f"Alpaca 연결 실패: {exc}") from exc
        if response.status_code == 429:
            raise MarketDataError(
                "Alpaca 요청 제한에 도달했습니다. 잠시 후 다시 시도하세요."
            )
        if response.status_code in {401, 403}:
            raise MarketDataError("Alpaca 인증 또는 데이터 이용 권한을 확인하세요.")
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketDataError(f"Alpaca 응답을 읽지 못했습니다: {exc}") from exc
        if not isinstance(payload, dict):
            raise MarketDataError("Alpaca가 예상하지 못한 형식으로 응답했습니다.")
        return payload

    @staticmethod
    def _parse_mover(item: dict[str, Any]) -> MarketMover:
        return MarketMover(
            symbol=str(item.get("symbol", "")).upper(),
            price=_number(item.get("price")),
            change_percent=_number(
                item.get("percent_change", item.get("change_percent"))
            ),
        )

    @staticmethod
    def _parse_news(item: dict[str, Any]) -> NewsItem:
        created = str(item.get("created_at", ""))
        try:
            created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
        return NewsItem(
            news_id=str(item.get("id", "")),
            headline=str(item.get("headline", "")).strip(),
            summary=str(item.get("summary", "")).strip(),
            url=str(item.get("url", "")).strip(),
            source=str(item.get("source", "")).strip(),
            created_at=created_at,
            symbols=tuple(str(value).upper() for value in item.get("symbols", [])),
        )


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
