from __future__ import annotations

from dataclasses import dataclass

from us_market_bot.models import DomesticCandidate, HotStock, MarketSnapshot


@dataclass(frozen=True)
class ImpactRule:
    sector: str
    us_symbols: tuple[str, ...]
    keywords: tuple[str, ...]
    korean_names: tuple[str, ...]
    rationale: str


RULES = (
    ImpactRule(
        sector="반도체·장비",
        us_symbols=("SMH", "NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "ASML"),
        keywords=("semiconductor", "chip", "foundry", "memory", "ai accelerator"),
        korean_names=("삼성전자", "SK하이닉스", "한미반도체"),
        rationale="미국 반도체 업종과 AI·메모리 투자 심리의 국내 전이 가능성",
    ),
    ImpactRule(
        sector="자동차·이차전지",
        us_symbols=("TSLA", "RIVN", "LCID", "GM", "F"),
        keywords=("electric vehicle", "battery", "ev sales", "lithium"),
        korean_names=("현대차", "기아", "LG에너지솔루션", "삼성SDI", "POSCO퓨처엠"),
        rationale="미국 전기차 수요와 배터리 공급망 심리의 국내 전이 가능성",
    ),
    ImpactRule(
        sector="정유·화학·운송",
        us_symbols=("XLE", "XOM", "CVX", "OXY", "DAL", "UAL"),
        keywords=("crude oil", "oil price", "opec", "jet fuel"),
        korean_names=("SK이노베이션", "S-Oil", "대한항공", "한진칼"),
        rationale="유가 변화는 정유사에는 우호적일 수 있지만 항공·운송에는 비용 부담 가능성",
    ),
    ImpactRule(
        sector="바이오·헬스케어",
        us_symbols=("XLV", "XBI", "LLY", "NVO", "MRNA", "PFE"),
        keywords=("fda", "clinical trial", "obesity drug", "biotech", "pharma"),
        korean_names=("삼성바이오로직스", "셀트리온", "알테오젠"),
        rationale="글로벌 제약·바이오 위험선호와 기술수출 기대의 국내 전이 가능성",
    ),
    ImpactRule(
        sector="방산",
        us_symbols=("LMT", "RTX", "NOC", "GD"),
        keywords=("defense contract", "missile", "defense spending", "geopolitical"),
        korean_names=("한화에어로스페이스", "LIG넥스원", "현대로템"),
        rationale="미국 방산 수주와 지정학적 위험 변화에 따른 업종 관심 가능성",
    ),
    ImpactRule(
        sector="원전·전력 인프라",
        us_symbols=("CEG", "VST", "CCJ", "SMR", "GEV"),
        keywords=("nuclear", "uranium", "power grid", "electricity demand"),
        korean_names=("두산에너빌리티", "HD현대일렉트릭", "LS ELECTRIC"),
        rationale="전력 수요·원전·전력망 투자 기대의 국내 관련 업종 전이 가능성",
    ),
    ImpactRule(
        sector="인터넷·플랫폼·콘텐츠",
        us_symbols=("META", "GOOGL", "AMZN", "NFLX", "SNAP"),
        keywords=("digital advertising", "streaming", "cloud", "platform"),
        korean_names=("NAVER", "카카오", "크래프톤", "하이브"),
        rationale="광고·클라우드·콘텐츠 소비 지표 변화의 국내 플랫폼 심리 전이 가능성",
    ),
)


def domestic_candidates(
    snapshot: MarketSnapshot,
    hot_stocks: tuple[HotStock, ...],
    *,
    limit: int = 5,
) -> tuple[DomesticCandidate, ...]:
    hot_by_symbol = {item.symbol: item for item in hot_stocks}
    benchmark_by_symbol = {item.symbol: item.change_percent for item in snapshot.benchmarks}
    headlines = " ".join(item.headline.casefold() for item in snapshot.news)
    candidates: list[tuple[float, DomesticCandidate]] = []

    for rule in RULES:
        symbol_hits = [symbol for symbol in rule.us_symbols if symbol in hot_by_symbol]
        benchmark_hits = [
            symbol
            for symbol in rule.us_symbols
            if abs(benchmark_by_symbol.get(symbol, 0.0)) >= 0.8
        ]
        keyword_hits = [keyword for keyword in rule.keywords if keyword in headlines]
        triggers = tuple(dict.fromkeys((*symbol_hits, *benchmark_hits, *keyword_hits[:2])))
        if not triggers:
            continue

        changes = [
            hot_by_symbol[symbol].change_percent
            for symbol in symbol_hits
            if hot_by_symbol[symbol].change_percent is not None
        ]
        changes.extend(benchmark_by_symbol[symbol] for symbol in benchmark_hits)
        average = sum(changes) / len(changes) if changes else 0.0
        if average >= 1:
            outlook = "긍정 가능성"
        elif average <= -1:
            outlook = "부정 가능성"
        else:
            outlook = "변동성 확대 가능성"

        evidence_points = len(symbol_hits) + len(benchmark_hits) + min(len(keyword_hits), 2)
        confidence = "높음" if evidence_points >= 3 else "보통"
        score = evidence_points * 10 + abs(average)
        candidates.append(
            (
                score,
                DomesticCandidate(
                    sector=rule.sector,
                    names=rule.korean_names,
                    outlook=outlook,
                    confidence=confidence,
                    triggers=triggers,
                    rationale=rule.rationale,
                ),
            )
        )

    candidates.sort(key=lambda item: (-item[0], item[1].sector))
    return tuple(item[1] for item in candidates[: max(1, limit)])

