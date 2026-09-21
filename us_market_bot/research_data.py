"""Read-only, explicitly dated evidence. No broker trading endpoints."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests

from us_market_bot.calendar import prior_sessions
from us_market_bot.providers.alpaca import AlpacaMarketData, MarketDataError

UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")

EQUITIES = {
    "AAPL": ("애플", "기술"),
    "MSFT": ("마이크로소프트", "기술"),
    "NVDA": ("엔비디아", "반도체"),
    "AMD": ("AMD", "반도체"),
    "AVGO": ("브로드컴", "반도체"),
    "GOOGL": ("알파벳", "플랫폼"),
    "META": ("메타", "플랫폼"),
    "AMZN": ("아마존", "소비"),
    "TSLA": ("테슬라", "자동차"),
    "XOM": ("엑슨모빌", "에너지"),
    "JPM": ("JP모건", "금융"),
    "LLY": ("일라이릴리", "헬스케어"),
    "WMT": ("월마트", "소비"),
    "LMT": ("록히드마틴", "방산"),
    "CEG": ("콘스텔레이션 에너지", "전력"),
    "MU": ("마이크론", "반도체"),
    "F": ("포드", "자동차"),
    "DAL": ("델타항공", "항공"),
}


def valid_number(value):
    result = float(value)
    if not isfinite(result):
        raise ValueError("비정상 수치")
    return result


class ResearchData:
    def __init__(self, settings):
        self.client = AlpacaMarketData(
            settings.alpaca_key_id,
            settings.alpaca_secret_key,
            feed=settings.alpaca_feed,
        )
        self.http = requests.Session()
        self.http.headers["User-Agent"] = (
            "PersonalMarketBriefing/0.3 (https://github.com/junyeolfilm/us-stock-monitoring-bot)"
        )
        self.warnings = []

    def regular_history(self, symbols, closed, *, count=26):
        sessions = prior_sessions("XNYS", closed.date, count)
        params = dict(
            symbols=",".join(sorted(set(symbols))),
            timeframe="5Min",
            start=sessions[0].open.isoformat(),
            end=closed.close.isoformat(),
            adjustment="raw",
            feed="sip",
            limit=10000,
            sort="asc",
            asof=closed.date,
        )
        output = {}
        for page in range(80):
            payload = self.client._get("/v2/stocks/bars", params)
            for symbol, bars in payload.get("bars", {}).items():
                output.setdefault(symbol, []).extend(bars)
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        else:
            raise MarketDataError(
                "시세 페이지 상한 도달: 일부 자료로 가격을 계산하지 않음"
            )
        return {s: aggregate_regular(bars, sessions) for s, bars in output.items()}

    def news(self, symbols, start, cutoff):
        params = dict(
            symbols=",".join(symbols),
            start=start.isoformat(),
            end=cutoff.isoformat(),
            sort="desc",
            limit=50,
        )
        values = []
        try:
            for _ in range(4):
                payload = self.client._get("/v1beta1/news", params)
                for item in payload.get("news", []):
                    created = datetime.fromisoformat(
                        item["created_at"].replace("Z", "+00:00")
                    )
                    updated = datetime.fromisoformat(
                        item.get("updated_at", item["created_at"]).replace(
                            "Z", "+00:00"
                        )
                    )
                    if not (
                        created.tzinfo
                        and updated.tzinfo
                        and start <= created <= cutoff
                        and updated <= cutoff
                    ):
                        continue
                    values.append(
                        dict(
                            title=clean(item.get("headline", "")),
                            url=safe_url(item.get("url", "")),
                            at=created.isoformat(),
                            updated_at=updated.isoformat(),
                            source=clean(item.get("source", "")),
                            symbols=item.get("symbols", []),
                            kind="외부 보도",
                            content="",
                        )
                    )
                token = payload.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
            else:
                self.warnings.append("뉴스 페이지 상한: 확인한 기사만 사용")
        except (MarketDataError, ValueError, KeyError):
            self.warnings.append("뉴스 수집 일부 실패: 확인되지 않은 원인 추정 보류")
        return values

    def official_events(self, start, cutoff):
        feeds = [
            ("연준", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
            ("미 노동통계국", "https://www.bls.gov/feed/bls_latest.rss"),
        ]
        result = []
        for name, url in feeds:
            try:
                response = self.http.get(url, timeout=(5, 15))
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.findall(".//item"):
                    when = parsedate_to_datetime(item.findtext("pubDate", ""))
                    if when.tzinfo is None or not start <= when <= cutoff:
                        continue
                    result.append(
                        dict(
                            title=clean(item.findtext("title", "")),
                            url=safe_url(item.findtext("link", "")),
                            at=when.isoformat(),
                            source=name,
                            kind="1차 자료",
                            symbols=[],
                        )
                    )
            except (requests.RequestException, ET.ParseError, ValueError, TypeError):
                self.warnings.append(f"{name} 공식 발표 확인 불가")
        return result[:5]

    def domestic(self, now, *, expected_day):
        indices = {}
        for symbol in ("KOSPI", "KOSDAQ"):
            url = f"https://m.stock.naver.com/api/index/{symbol}/basic"
            try:
                r = self.http.get(url, timeout=(5, 20))
                r.raise_for_status()
                p = r.json()
                at = datetime.fromisoformat(p["localTradedAt"])
                if (
                    at.tzinfo is None
                    or at > now
                    or str(at.date()) != expected_day
                    or p.get("marketStatus") != "CLOSE"
                ):
                    raise ValueError("미완료/지연 지수")
                indices[symbol] = dict(
                    symbol=symbol,
                    name=clean(p["stockName"]),
                    close=number(p["closePrice"]),
                    change=number(p["fluctuationsRatio"]),
                    at=at.isoformat(),
                    source=url,
                    session="KRX 정규장 지수",
                    delay="제공처 지연·정정 가능",
                )
            except (requests.RequestException, KeyError, ValueError):
                self.warnings.append(f"{symbol} {expected_day} 정규장 마감 미확인")
        stocks = []
        # Current rankings are never backfilled into a historical report.
        if str(now.astimezone(KST).date()) == expected_day:
            for market in ("KOSPI", "KOSDAQ"):
                url = f"https://m.stock.naver.com/api/stocks/priceTop/{market}"
                try:
                    r = self.http.get(
                        url, params={"page": 1, "pageSize": 10}, timeout=(5, 20)
                    )
                    r.raise_for_status()
                    for p in r.json().get("stocks", []):
                        if p.get("stockEndType") != "stock":
                            continue
                        stocks.append(
                            dict(
                                symbol=p["itemCode"],
                                name=clean(p["stockName"]),
                                market=market,
                                change=number(p["fluctuationsRatio"]),
                                close=number(
                                    p.get("closePriceRaw", p.get("closePrice"))
                                ),
                                volume=number(
                                    p.get(
                                        "accumulatedTradingVolumeRaw",
                                        p.get("accumulatedTradingVolume", 0),
                                    )
                                ),
                                value=number(p.get("accumulatedTradingValueRaw", 0)),
                                source=f"https://stock.naver.com/domestic/stock/{p['itemCode']}",
                                at=now.isoformat(),
                                session="정규장/장후/대체거래소 혼합 여부 미검증",
                                verified_regular=False,
                            )
                        )
                except (requests.RequestException, KeyError, ValueError):
                    self.warnings.append(f"{market} 거래대금 순위 수집 실패")
        return dict(
            indices=indices,
            stocks=sorted(stocks, key=lambda p: -p["value"])[:5],
            asof=now.isoformat(),
            date=expected_day,
        )


def aggregate_regular(raw, sessions):
    groups = {s.date: {} for s in sessions}
    by_date = {s.date: s for s in sessions}
    for row in raw:
        t = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
        day = str(t.astimezone(NY).date())
        s = by_date.get(day)
        if not s or not s.open <= t < s.close:
            continue
        fields = {k: valid_number(row[k]) for k in ("o", "h", "l", "c", "v")}
        if (
            min(fields[k] for k in ("o", "h", "l", "c")) <= 0
            or fields["v"] < 0
            or fields["h"] < max(fields["o"], fields["c"], fields["l"])
            or fields["l"] > min(fields["o"], fields["c"])
        ):
            raise MarketDataError("시세 OHLC 무결성 오류")
        groups[day][t] = fields
    result = []
    for s in sessions:
        items = sorted(groups[s.date].items())
        if not items:
            continue
        complete = (
            len(items) == int((s.close - s.open).total_seconds() / 300)
            and items[0][0] == s.open
            and items[-1][0] == s.close - timedelta(minutes=5)
        )
        result.append(
            dict(
                date=s.date,
                asof=s.close.isoformat(),
                open=items[0][1]["o"],
                high=max(b["h"] for _, b in items),
                low=min(b["l"] for _, b in items),
                close=items[-1][1]["c"],
                volume=sum(b["v"] for _, b in items),
                complete=complete,
                bar_count=len(items),
                adjustment="raw",
                feed="sip",
                intraday=[dict(at=t.isoformat(), **b) for t, b in items]
                if s == sessions[-1]
                else [],
            )
        )
    return result


def number(value):
    return valid_number(str(value).replace(",", ""))


def clean(text):
    # External content stays inert, cannot ping Discord or add markdown commands.
    return (
        str(text)
        .replace("@", "＠")
        .replace("`", "")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\n", " ")[:180]
    )


def safe_url(url):
    from urllib.parse import urlparse

    p = urlparse(str(url))
    return (
        str(url).replace(")", "%29")
        if p.scheme == "https" and p.netloc and not p.username
        else ""
    )
