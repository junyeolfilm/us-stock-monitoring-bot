"""Cash-equity research only. No order client and no execution functions."""

from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import floor, isfinite
from us_market_bot.calendar import prior_sessions

RULE_SOURCES = {
    "KR": "https://regulation.krx.co.kr/contents/RGL/03/03020100/RGL03020100.jsp",
    "US": "https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126",
}


def tick(price: float, market: str, day: str) -> Decimal:
    if price <= 0 or not isfinite(price):
        raise ValueError("가격 오류")
    # Rules must be reviewed before the currently announced US transition.
    if market == "US":
        if day >= "2027-11-01":
            raise ValueError("미국 호가 규칙 재확인 필요")
        return Decimal("0.01") if price >= 1 else Decimal("0.0001")
    if market in {"KOSPI", "KOSDAQ"}:
        for bound, size in (
            (2000, 1),
            (5000, 5),
            (20000, 10),
            (50000, 50),
            (200000, 100),
            (500000, 500),
            (float("inf"), 1000),
        ):
            if price < bound:
                return Decimal(size)
    raise ValueError("지원하지 않는 현금 주식 시장")


def rounded(price, market, day, *, up=False):
    size = tick(price, market, day)
    value = (Decimal(str(round(price, 8))) / size).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR
    ) * size
    # Crossing a price tier must still land on a valid tick.
    size = tick(float(value), market, day)
    return float(
        (value / size).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
        * size
    )


def atr(bars, period=14):
    if len(bars) < period + 1:
        raise ValueError("변동성 계산에 필요한 15거래일 부족")
    ranges = [
        max(
            b["high"] - b["low"],
            abs(b["high"] - a["close"]),
            abs(b["low"] - a["close"]),
        )
        for a, b in zip(bars[:-1], bars[1:])
    ]
    return sum(ranges[-period:]) / period


def quantity(entry, stop, profile, *, cash_left, sector_left):
    costs = profile.costs
    if any(not isfinite(float(v)) or float(v) < 0 for v in costs.values()):
        return 0
    cash_per_share = (
        entry * (1 + costs["buy_rate"])
        + costs["slippage_per_share"]
        + costs["fx_cost_per_share"]
    )
    loss_per_share = (
        entry
        - stop
        + entry * costs["buy_rate"]
        + stop * costs["sell_rate"]
        + 2 * costs["slippage_per_share"]
        + costs["fx_cost_per_share"]
    )
    if loss_per_share <= 0:
        return 0
    return max(
        0,
        floor(
            min(
                profile.loss_budget / loss_per_share,
                profile.per_symbol_limit / cash_per_share,
                cash_left / cash_per_share,
                sector_left / cash_per_share,
            )
        ),
    )


def make_card(
    symbol,
    name,
    sector,
    bars,
    *,
    market,
    session_date,
    valid_session,
    feed,
    profile,
    now,
    currency="USD",
):
    if feed != "sip" or market != "US":
        return None, "정규장·원시가격·시장 전체 거래량을 검증하지 못해 가격 제안 보류"
    if len(bars) < 25 or bars[-1]["date"] != session_date:
        return None, "25개 정규장 또는 최신 완료 세션 자료 부족"
    if [b["date"] for b in bars[-25:]] != [
        s.date for s in prior_sessions("XNYS", session_date, 25)
    ]:
        return None, "연속 정규장 날짜 누락: 가격 제안 보류"
    if any(not b.get("complete") or b["volume"] <= 0 for b in bars[-25:]):
        return None, "정규장 봉 누락/거래량 부족"
    if any(abs(b["open"] / a["close"] - 1) > 0.25 for a, b in zip(bars[:-1], bars[1:])):
        return None, "큰 갭/권리변동 가능성: 원시·수정주가 대조 전 보류"
    if profile.markets and "US" not in profile.markets:
        return None, "설정된 매매 시장이 아님"
    close = bars[-1]["close"]
    volatility = atr(bars)
    if volatility <= 0 or sum(b["volume"] for b in bars[-20:]) / 20 < 100000:
        return None, "유동성/변동성 기준 미달"
    recent = bars[-21:]
    lows = [
        b["low"]
        for a, b, c in zip(recent, recent[1:], recent[2:])
        if b["low"] <= min(a["low"], c["low"]) and b["low"] < close
    ]
    highs = sorted(
        set(
            b["high"]
            for a, b, c in zip(recent, recent[1:], recent[2:])
            if b["high"] >= max(a["high"], c["high"]) and b["high"] > close
        )
    )
    if not lows or len(highs) < 2:
        return None, "실제 관측된 지지점 또는 2개 저항점 부족"
    support = max(lows)
    entry = rounded(min(close, support + 0.10 * volatility), market, session_date)
    stop = rounded(support - 0.25 * volatility, market, session_date)
    t1, t2 = [rounded(p, market, session_date) for p in highs[:2]]
    if not (0 < stop < entry < t1 < t2) or close - entry > 0.75 * volatility:
        return None, "현재 가격에서 지지점이 멀거나 가격 순서 부적합"
    rr1, rr2 = (t1 - entry) / (entry - stop), (t2 - entry) / (entry - stop)
    if rr1 < 1.5:
        return None, "관측된 1차 저항 기준 손익비 1.5 미만"
    holding = next(
        (
            x
            for x in (profile.holdings or [])
            if x.get("symbol") == symbol
            and x.get("market") == "US"
            and x.get("quantity", 0) > 0
        ),
        None,
    )
    if holding:
        entry = rounded(close, market, session_date)
        rr1, rr2 = (t1 - entry) / (entry - stop), (t2 - entry) / (entry - stop)
    return dict(
        id=f"{valid_session.date}:{symbol}",
        symbol=symbol,
        name=name,
        sector=sector,
        market=market,
        currency=currency,
        judgment="보유 관리"
        if holding
        else "신규 진입 검토(보유 여부 미확인)"
        if profile.holdings is None
        else "신규 진입 검토",
        reference=close,
        reference_at=bars[-1]["asof"],
        feed=feed,
        adjustment="raw",
        session="regular",
        horizon=profile.horizon or "가정: 2~5거래일",
        entry_low=rounded(support, market, session_date),
        entry=entry,
        stop_trigger=stop,
        stop_order_price=None,
        target1=t1,
        target2=t2,
        weights=[50, 50],
        rr1=rr1,
        rr2=rr2,
        quantity=None,
        atr14=volatility,
        support=support,
        resistance=highs[:2],
        source="https://docs.alpaca.markets/us/reference/stockbars",
        valid_from=valid_session.open.isoformat(),
        valid_until=valid_session.close.isoformat(),
        status="검토 대기",
        entry_condition="정규장 개장 후 지지 유지·최신 공시·호가 재확인 필요. 가격 도달만으로 진입 판단 불가",
        order_method="증권사 주문 지원 미확인: 수동 가격 확인/증권사 알림 후 판단(봇 실시간 알림 없음). 매수 지정가 후보이며 시간 예약/돌파 조건부 주문과 다름",
        stop_method="가설 무효 관찰 가격. 일반 매도 지정가로 설정하지 않음; 가격 확인 후 수동 판단",
        cancellation="시초가가 지지점 또는 손절 아래, 1차 목표 이상으로 갭 발생, 새 공시/권리변동, 유효 세션 종료 시 폐기·재분석",
        costs="수수료·세금·환전·슬리피지 미반영; 손익비는 가격 간 비율",
        holding=holding,
    ), None


def allocate(cards, profile, now):
    if not profile.can_size(now, "USD"):
        return
    # Even with a complete profile, unknown/open orders block size allocation.
    if profile.open_orders:
        return
    cash = profile.available_cash
    sector_used = {}
    for position in profile.holdings:
        if "market_value" not in position or "sector" not in position:
            return
        sector_used[position["sector"]] = (
            sector_used.get(position["sector"], 0) + position["market_value"]
        )
    for card in cards:
        if card["holding"]:
            # No new/additional buys or independent exits for an existing holding.
            card["quantity"] = None
            continue
        available = profile.sector_limit - sector_used.get(card["sector"], 0)
        n = quantity(
            card["entry"],
            card["stop_trigger"],
            profile,
            cash_left=cash,
            sector_left=max(0, available),
        )
        card["quantity"] = n
        spent = n * (
            card["entry"] * (1 + profile.costs["buy_rate"])
            + profile.costs["slippage_per_share"]
            + profile.costs["fx_cost_per_share"]
        )
        cash -= spent
        sector_used[card["sector"]] = sector_used.get(card["sector"], 0) + spent
        card["costs"] = (
            "수량에 설정된 비용 반영; 표시 손익비는 비용 전 가격비. 갭 손실은 예산 초과 가능"
        )


def review_card(card, bar, now):
    if now < datetime.fromisoformat(card["valid_from"]):
        return {
            "status": "판단 유보",
            "decision": "유지",
            "reason": "대상 시장의 유효 정규장이 아직 시작되지 않음",
        }
    if (
        not bar
        or bar.get("asof") is None
        or datetime.fromisoformat(bar["asof"]) > now
        or not bar.get("complete")
    ):
        return {
            "status": "판단 유보",
            "decision": "재검증",
            "reason": "정규장 고가·저가·시각 자료 부족",
        }
    touched = bar["low"] <= card["entry"] <= bar["high"]
    take = bar["high"] >= card["target1"]
    stop = bar["low"] <= card["stop_trigger"]
    ambiguous = take and stop
    gap = bar["open"] < card["entry_low"] or bar["open"] >= card["target1"]
    return {
        "status": "판단 유보" if ambiguous or touched else "미도달",
        "decision": "폐기·재분석",
        "reason": f"가상 시나리오: 진입 가격 도달={touched}, 익절 도달={take}, 손절 도달={stop}, 갭 취소={gap}. "
        + ("익절·손절 순서 불명. " if ambiguous else "")
        + "추가 진입 조건/실제 체결은 확인되지 않음. 원문 가격안 유효기간 종료; 새 가격은 새 버전으로만 생성",
    }
