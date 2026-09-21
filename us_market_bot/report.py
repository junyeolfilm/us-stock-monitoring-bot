from __future__ import annotations

from datetime import datetime

from us_market_bot.domestic import domestic_candidates
from us_market_bot.models import DomesticSnapshot, MarketSnapshot
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
    previous_domestic: DomesticSnapshot | None = None,
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

    lines.extend(("", "🔭 **오늘 국내장 예상 시나리오**"))
    lines.extend(_domestic_forecast_lines(snapshot, previous_domestic, domestic))

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


def build_domestic_report(
    snapshot: DomesticSnapshot,
    *,
    local_now: datetime,
) -> str:
    lines = [
        f"🇰🇷 **국내 증시 마감 브리핑 · {snapshot.market_date}**",
        "_정규장 마감 후 수집 시점 기준 · 투자 추천이 아닌 시장 동향 정보입니다._",
        "",
        "**주요 지수**",
    ]
    if snapshot.indices:
        lines.extend(
            f"• {item.name} `{item.price:,.2f}` · `{item.change_percent:+.2f}%`"
            for item in snapshot.indices
        )
    else:
        lines.append("• 지수 데이터를 확인하지 못했습니다.")

    lines.extend(("", "**마감 흐름 해석**"))
    lines.extend(_domestic_close_lines(snapshot))

    lines.extend(("", "**거래대금 상위**"))
    if snapshot.value_leaders:
        for item in snapshot.value_leaders[:8]:
            value = _format_krw(item.trading_value)
            lines.append(
                f"• [{item.name}]({item.url}) `{item.change_percent:+.2f}%` · {value}"
            )
    else:
        lines.append("• 거래대금 상위 데이터를 확인하지 못했습니다.")

    lines.extend(("", "**급등 종목**"))
    lines.extend(_stock_move_lines(snapshot.gainers[:5]))
    lines.extend(("", "**급락 종목**"))
    lines.extend(_stock_move_lines(snapshot.losers[:5]))
    lines.extend(
        (
            "",
            "`네이버 증권 공개 시세 기준. 데이터 제공 지연이나 정정 가능성이 있습니다.`",
            "`개별 종목 등락은 투자 추천이 아니며 다음 미국장 브리핑의 국내장 시나리오 참고 자료로만 사용합니다.`",
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


def _domestic_close_lines(snapshot: DomesticSnapshot) -> list[str]:
    changes = {item.symbol: item.change_percent for item in snapshot.indices}
    kospi = changes.get("KOSPI", 0.0)
    kosdaq = changes.get("KOSDAQ", 0.0)
    if kospi >= 1 and kosdaq >= 1:
        tone = "양 시장이 함께 강세를 보인 위험선호 장세"
    elif kospi <= -1 and kosdaq <= -1:
        tone = "양 시장이 함께 약세를 보인 위험회피 장세"
    elif kospi * kosdaq < 0:
        tone = "코스피와 코스닥 방향이 엇갈린 차별화 장세"
    else:
        tone = "지수 방향성이 제한된 혼조 장세"
    leaders = ", ".join(item.name for item in snapshot.value_leaders[:3])
    output = [f"• **{tone}**"]
    if leaders:
        output.append(f"• 자금 집중 관찰: {leaders}")
    return output


def _domestic_forecast_lines(
    us_snapshot: MarketSnapshot,
    previous: DomesticSnapshot | None,
    candidates,
) -> list[str]:
    changes = {item.symbol: item.change_percent for item in us_snapshot.benchmarks}
    weighted = (
        changes.get("SPY", 0.0) * 0.35
        + changes.get("QQQ", 0.0) * 0.35
        + changes.get("SMH", 0.0) * 0.20
        + changes.get("IWM", 0.0) * 0.10
    )
    signals = [
        changes[symbol]
        for symbol in ("SPY", "QQQ", "SMH", "IWM")
        if symbol in changes
    ]
    agreement = sum(value > 0 for value in signals) - sum(value < 0 for value in signals)
    if weighted >= 0.6:
        scenario = "상승 우세"
    elif weighted <= -0.6:
        scenario = "하락 경계"
    else:
        scenario = "혼조 가능성"
    confidence = "보통"
    if abs(weighted) >= 1.2 and abs(agreement) >= 3:
        confidence = "높음"
    elif abs(weighted) < 0.3 or abs(agreement) <= 1:
        confidence = "낮음"

    output = [
        f"• 기본 시나리오: **{scenario}** · 신뢰도 **{confidence}**",
        f"• 미국장 합성 신호: `{weighted:+.2f}` "
        "(S&P500·나스닥100·반도체·중소형주 가중)",
    ]
    if previous is not None:
        prior_moves = ", ".join(
            f"{item.name} {item.change_percent:+.2f}%" for item in previous.indices
        )
        output.append(f"• 전일 국내장({previous.market_date}): {prior_moves}")
        prior_names = {item.name for item in previous.value_leaders[:10]}
        overlaps = [
            name
            for candidate in candidates
            for name in candidate.names
            if name in prior_names
        ]
        if overlaps:
            output.append(
                "• 연속 관찰 후보: " + ", ".join(dict.fromkeys(overlaps))
            )
    else:
        output.append("• 저장된 전일 국내장 마감 자료가 없어 미국장 신호만 반영했습니다.")

    sectors = ", ".join(item.sector for item in candidates[:3])
    if sectors:
        output.append(f"• 개장 전 우선 확인 업종: {sectors}")
    output.append(
        "• 반대 조건: 장 초 지수 방향·원화·외국인 수급이 미국장 신호와 반대로 움직이면 시나리오를 무효화"
    )
    return output


def _stock_move_lines(items) -> list[str]:
    if not items:
        return ["• 해당 데이터를 확인하지 못했습니다."]
    return [
        f"• [{item.name}]({item.url}) `{item.change_percent:+.2f}%` · "
        f"거래대금 {_format_krw(item.trading_value)}"
        for item in items
    ]


def _format_krw(value: int) -> str:
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}조원"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.0f}억원"
    if value >= 10_000:
        return f"{value / 10_000:.0f}만원"
    return f"{value:,}원"
