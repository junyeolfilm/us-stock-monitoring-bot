from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from us_market_bot.models import (
    DomesticIndexMove,
    DomesticSnapshot,
    DomesticStockMove,
)
from us_market_bot.providers.alpaca import MarketDataError


class NaverDomesticMarketData:
    """Read end-of-session Korean market metadata from public Naver pages."""

    base_url = "https://m.stock.naver.com/api"
    markets = ("KOSPI", "KOSDAQ")

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (5, 20),
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.update(
            {
                "User-Agent": (
                    "USDomesticMarketBriefingBot/0.2 "
                    "(personal end-of-day market summary)"
                ),
                "Accept": "application/json",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
            }
        )

    def collect(self, *, list_size: int = 10) -> DomesticSnapshot:
        indices_payload = [
            self._get(f"/index/{market}/basic", {}) for market in self.markets
        ]
        indices = tuple(self._parse_index(item) for item in indices_payload)
        traded_dates = [
            self._local_datetime(item.get("localTradedAt")).date().isoformat()
            for item in indices_payload
            if item.get("localTradedAt")
        ]
        if not traded_dates:
            raise MarketDataError("국내 지수의 거래일을 확인하지 못했습니다.")

        gainers: list[DomesticStockMove] = []
        losers: list[DomesticStockMove] = []
        value_leaders: list[DomesticStockMove] = []
        for market in self.markets:
            gainers.extend(self._stock_list("up", market, list_size))
            losers.extend(self._stock_list("down", market, list_size))
            value_leaders.extend(self._stock_list("priceTop", market, list_size))

        gainers.sort(key=lambda item: (-item.change_percent, -item.trading_value))
        losers.sort(key=lambda item: (item.change_percent, -item.trading_value))
        value_leaders.sort(key=lambda item: -item.trading_value)
        return DomesticSnapshot(
            market_date=max(traded_dates),
            collected_at=datetime.now(timezone.utc),
            indices=indices,
            gainers=tuple(gainers[:list_size]),
            losers=tuple(losers[:list_size]),
            value_leaders=tuple(value_leaders[:list_size]),
        )

    def _stock_list(
        self, sort_type: str, market: str, page_size: int
    ) -> tuple[DomesticStockMove, ...]:
        payload = self._get(
            f"/stocks/{sort_type}/{market}",
            {"page": 1, "pageSize": max(10, min(page_size * 3, 30))},
        )
        values = payload.get("stocks", [])
        return tuple(
            self._parse_stock(item, market)
            for item in values
            if item.get("itemCode")
            and item.get("stockName")
            and item.get("stockEndType", "stock") == "stock"
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{path}", params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise MarketDataError(f"국내 시세 연결 실패: {exc}") from exc
        if response.status_code == 429:
            raise MarketDataError("국내 시세 요청 제한에 도달했습니다.")
        if response.status_code in {401, 403}:
            raise MarketDataError("국내 시세 제공처가 요청을 거부했습니다.")
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketDataError(f"국내 시세 응답을 읽지 못했습니다: {exc}") from exc
        if not isinstance(payload, dict):
            raise MarketDataError("국내 시세가 예상하지 못한 형식으로 응답했습니다.")
        return payload

    @classmethod
    def _parse_index(cls, item: dict[str, Any]) -> DomesticIndexMove:
        return DomesticIndexMove(
            symbol=str(item.get("itemCode", "")),
            name=str(item.get("stockName", "")),
            price=cls._number(item.get("closePrice")),
            change_percent=cls._number(item.get("fluctuationsRatio")),
            market_status=str(item.get("marketStatus", "UNKNOWN")),
        )

    @classmethod
    def _parse_stock(
        cls, item: dict[str, Any], market: str
    ) -> DomesticStockMove:
        code = str(item.get("itemCode", ""))
        return DomesticStockMove(
            code=code,
            name=str(item.get("stockName", "")).strip(),
            market=market,
            price=cls._number(item.get("closePriceRaw", item.get("closePrice"))),
            change_percent=cls._number(item.get("fluctuationsRatio")),
            volume=cls._integer(
                item.get("accumulatedTradingVolumeRaw", item.get("accumulatedTradingVolume"))
            ),
            trading_value=cls._integer(item.get("accumulatedTradingValueRaw")),
            url=str(
                item.get(
                    "newPcUrl", f"https://stock.naver.com/domestic/stock/{code}"
                )
            ),
        )

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _local_datetime(value: Any) -> datetime:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.now().astimezone()
