from __future__ import annotations

from collections import Counter

from us_market_bot.models import HotStock, MarketSnapshot


def rank_hot_stocks(
    snapshot: MarketSnapshot,
    watchlist: tuple[str, ...],
    *,
    limit: int = 10,
) -> tuple[HotStock, ...]:
    movers = {item.symbol: item for item in (*snapshot.gainers, *snapshot.losers)}
    active_ranks = {item.symbol: rank for rank, item in enumerate(snapshot.actives, 1)}
    news_counts: Counter[str] = Counter(
        symbol for item in snapshot.news for symbol in item.symbols
    )
    watch = set(watchlist)
    symbols = set(movers) | set(active_ranks) | set(news_counts)
    ranked: list[HotStock] = []

    for symbol in symbols:
        mover = movers.get(symbol)
        active_rank = active_ranks.get(symbol)
        news_count = news_counts[symbol]
        change = None if mover is None else mover.change_percent
        price = None if mover is None else mover.price
        score = 0.0
        reasons: list[str] = []

        if change is not None:
            move_points = min(abs(change) * 6.0, 40.0)
            score += move_points
            if abs(change) >= 2:
                reasons.append(f"등락 {change:+.1f}%")
        if active_rank is not None:
            score += max(3.0, 25.0 - (active_rank - 1) * 0.7)
            if active_rank <= 20:
                reasons.append(f"거래량 {active_rank}위")
        if news_count:
            score += min(news_count * 5.0, 25.0)
            reasons.append(f"관련 뉴스 {news_count}건")
        if symbol in watch:
            score += 10.0
            reasons.append("관심 종목")
        if score < 18:
            continue

        ranked.append(
            HotStock(
                symbol=symbol,
                price=price,
                change_percent=change,
                heat_score=round(min(score, 100.0), 1),
                active_rank=active_rank,
                news_count=news_count,
                reasons=tuple(reasons),
            )
        )

    ranked.sort(key=lambda item: (-item.heat_score, item.symbol))
    return tuple(ranked[: max(1, limit)])

