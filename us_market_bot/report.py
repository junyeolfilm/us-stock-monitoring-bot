from __future__ import annotations

from datetime import datetime

from us_market_bot.domestic import domestic_candidates
from us_market_bot.models import MarketSnapshot
from us_market_bot.scoring import rank_hot_stocks


BENCHMARK_LABELS = {
    "SPY": "S&P500",
    "QQQ": "나스닥100",
    "DIA": "다우",
    "IWM": "러셀2000",
    "SMH": "반도체",
    "XLE": "에너지",
    "XLF": "금융",
    "XLV": "헬스케어",
}


def build_report(
    snapshot: MarketSnapshot,
    watchlist: tuple[str, ...],
    *,
    local_now: datetime,
) -> str:
    hot = rank_hot_stocks(snapshot, watchlist)
    domestic = domestic_candidates(snapshot, hot)
    weekend = local_now.weekday() in {0, 6}
    basis = "최근 미국 거래일 기준" if weekend else "미국장 마감 기준"
    lines = [
        f"🇺🇸 **미국 증시 아침 브리핑 · {local_now:%Y-%m-%d}**",
        f"_{basis} · 투자 추천이 아닌 시장 동향 정보입니다._",
        "",
        "**주요 지수·업종**",
    ]
    if snapshot.benchmarks:
        lines.extend(
            f"• {BENCHMARK_LABELS.get(item.symbol, item.symbol)} `{item.change_percent:+.2f}%`"
            for item in snapshot.benchmarks
        )
    else:
        lines.append("• 지수 데이터를 확인하지 못했습니다.")

    lines.extend(("", "**오늘의 화제 종목**"))
    if hot:
        for item in hot[:8]:
            change = "등락 확인 중" if item.change_percent is None else f"{item.change_percent:+.2f}%"
            reasons = " · ".join(item.reasons[:3])
            lines.append(f"• **{item.symbol}** `{change}` · Heat {item.heat_score:.0f} · {reasons}")
    else:
        lines.append("• 기준을 충족한 화제 종목이 없습니다.")

    lines.extend(("", "🇰🇷 **국내장 영향 관찰 후보**"))
    if domestic:
        for item in domestic:
            names = ", ".join(item.names[:5])
            triggers = ", ".join(item.triggers)
            lines.append(
                f"• **{item.sector} · {item.outlook} ({item.confidence})**\n"
                f"  {names}\n"
                f"  근거 신호: {triggers} · {item.rationale}"
            )
    else:
        lines.append("• 근거가 충분한 국내 영향 후보가 없습니다.")

    selected_news = _select_news(snapshot, {item.symbol for item in hot[:8]})
    lines.extend(("", "**주요 뉴스·근거**"))
    if selected_news:
        for item in selected_news[:5]:
            source = f" · {item.source}" if item.source else ""
            lines.append(f"• [{item.headline}]({item.url}){source}")
    else:
        lines.append("• 연결 가능한 주요 뉴스가 없습니다.")

    lines.extend(
        (
            "",
            "`Alpaca 시세·뉴스 기준. 무료 IEX 데이터는 미국 전체 거래소의 완전한 실시간 시세가 아닙니다.`",
            "`국내 종목은 상승·하락 예측이 아니라 개장 전 확인할 관찰 후보입니다.`",
        )
    )
    return "\n".join(lines)


def split_report(body: str, *, limit: int = 1900) -> tuple[str, ...]:
    if len(body) <= limit:
        return (body,)
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in body.splitlines():
        added = len(line) + 1
        if current and current_length + added > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += added
    if current:
        chunks.append("\n".join(current))
    return tuple(chunks)


def _select_news(snapshot: MarketSnapshot, hot_symbols: set[str]):
    matched = [
        item
        for item in snapshot.news
        if item.url and hot_symbols.intersection(item.symbols)
    ]
    return matched or [item for item in snapshot.news if item.url]

