"""Evidence-bound morning newspaper and immutable end-of-day review."""

from datetime import datetime, timedelta
import os

from us_market_bot.calendar import session, latest_closed, next_session
from us_market_bot.journal import Journal
from us_market_bot.prices import make_card, allocate, review_card, RULE_SOURCES
from us_market_bot.profile import load_profile
from us_market_bot.research_data import ResearchData, EQUITIES, KST, NY
from us_market_bot.providers.alpaca import MarketDataError

LABELS = {
    "SPY": "S&P500 ETF",
    "QQQ": "나스닥100 ETF",
    "DIA": "다우 ETF",
    "IWM": "미국 중소형주 ETF",
    "SMH": "반도체 ETF",
    "XLE": "에너지 ETF",
    "XLF": "금융 ETF",
    "XLV": "헬스케어 ETF",
}


def event_path(title):
    text = title.lower()
    for words, explanation in [
        (
            ("earnings", "revenue", "guidance", "profit", "quarter"),
            "실적·전망 보도 → 예상 현금흐름·기업가치 재평가 경로",
        ),
        (
            ("fda", "trial", "approval"),
            "임상·허가 보도 → 개발 성공 가능성·향후 매출 기대 변화 경로",
        ),
        (
            ("tariff", "sanction", "export", "policy"),
            "정책·통상 보도 → 판매 가능 시장·원가·마진 변화 경로",
        ),
        (
            ("upgrade", "downgrade", "price target", "analyst"),
            "증권사 평가 보도 → 단기 기대·투자심리 변화 경로(기업 실적 사실과 구분)",
        ),
        (
            ("contract", "deal", "order", "partnership"),
            "계약·협력 보도 → 매출 기대 변화 경로(실제 규모·수익성 추가 확인 필요)",
        ),
    ]:
        if any(word in text for word in words):
            return (
                "[봇 추론] "
                + explanation
                + ". 헤드라인 분류이며 원문 전체/인과 검증 미완료."
            )
    return "사건 유형과 가격 전이 경로를 확인할 근거 부족: 원인 추정 보류."


def event_price_order(event, item, opening):
    at = datetime.fromisoformat(event["at"])
    if at < datetime.fromisoformat(opening):
        return "보도 시각은 정규장 개장 전입니다. 개장 전 이미 반영됐거나 다른 사건과 겹쳤을 가능성이 있습니다."
    bars = item.get("intraday", [])
    before = [
        b for b in bars if datetime.fromisoformat(b["at"]) + timedelta(minutes=5) <= at
    ]
    after = [
        b
        for b in bars
        if at + timedelta(minutes=15)
        <= datetime.fromisoformat(b["at"]) + timedelta(minutes=5)
        <= at + timedelta(minutes=25)
    ]
    if not before or not after:
        return "보도 전후 비교 가능한 정규장 봉이 부족해 반응 시점 판단 유보."
    change = (after[0]["c"] / before[-1]["c"] - 1) * 100
    return f"[관찰] 보도 전 완료 봉 {before[-1]['at']} → 이후 봉 {after[0]['at']}: {change:+.2f}%. 같은 시간대 다른 사건은 통제하지 못했으므로 인과 확정 아님."


def moves(history, closed):
    result = {}
    for symbol, bars in history.items():
        if (
            len(bars) < 2
            or bars[-1]["date"] != closed.date
            or not all(b["complete"] for b in bars[-2:])
        ):
            continue
        b = bars[-1]
        result[symbol] = {
            **b,
            "symbol": symbol,
            "change": 100 * (b["close"] / bars[-2]["close"] - 1),
            "volume_ratio": b["volume"]
            / (sum(x["volume"] for x in bars[-21:-1]) / len(bars[-21:-1]))
            if len(bars) > 1
            else None,
        }
    return result


def hypotheses(us, day):
    result = []
    spy = us.get("SPY", {}).get("change")
    qqq = us.get("QQQ", {}).get("change")
    if spy is not None and abs(spy) >= 0.4:
        direction = 1 if spy > 0 else -1
        result.append(
            dict(
                id=f"{day}:H1",
                target="KOSPI",
                benchmark=None,
                direction=direction,
                kind="direction",
                expected=f"코스피가 전일 대비 {'상승' if direction > 0 else '하락'} 마감",
                invalidation="코스피가 반대 방향 마감하면 불일치. ±0.1% 이내는 방향성 부족",
                reason=f"SPY {spy:+.2f}%의 위험선호가 한국 지수에 전이될 가능성(봇 추론)",
            )
        )
    if spy is not None and qqq is not None and abs(qqq - spy) >= 0.3:
        direction = 1 if qqq > spy else -1
        result.append(
            dict(
                id=f"{day}:H2",
                target="KOSDAQ",
                benchmark="KOSPI",
                direction=direction,
                kind="relative",
                expected=f"코스닥 수익률이 코스피보다 {'높게' if direction > 0 else '낮게'} 마감",
                invalidation="반대 방향 0.1%p 초과이면 불일치. 같은 방향이지만 0.1%p 이내이면 일부 부합, 나머지는 판단 유보",
                reason=f"QQQ-SPY {qqq - spy:+.2f}%p 성장주 선호의 전이 가능성(봇 추론). 두 지수 업종 구성은 다름",
            )
        )
    return result[:3]


def review_hypothesis(hypothesis, indices):
    item = indices.get(hypothesis["target"])
    benchmark = (
        indices.get(hypothesis.get("benchmark"))
        if hypothesis.get("benchmark")
        else None
    )
    if not item or (hypothesis.get("benchmark") and not benchmark):
        return dict(
            id=hypothesis["id"],
            status="판단 유보",
            reason="비교에 필요한 정규장 지수 자료 부족",
        )
    value = item["change"] - (benchmark["change"] if benchmark else 0)
    signed = value * hypothesis["direction"]
    status = (
        "관찰과 부합"
        if signed > 0.1
        else "불일치"
        if signed < -0.1
        else "일부 부합"
        if signed > 0
        else "판단 유보"
    )
    comparison = f"{hypothesis['target']} {item['change']:+.2f}%"
    if benchmark:
        comparison += f", {hypothesis['benchmark']} {benchmark['change']:+.2f}% · 상대차 {value:+.2f}%p"
    return dict(
        id=hypothesis["id"],
        status=status,
        reason=comparison
        + "; 방향 일치가 미국 사건의 인과관계를 입증하지는 않음. 업종별 지수 자료 미확보",
    )


class ResearchService:
    def __init__(self, settings, database, data=None):
        self.settings = settings
        self.database = database
        database.initialize()
        self.journal = Journal(database.path)
        self.data = data or ResearchData(settings)
        self.profile = load_profile(
            os.environ.get("INVESTMENT_PROFILE_PATH", "investment_profile.json")
        )

    def morning(self, now=None, *, preview=False):
        now = (now or datetime.now(KST)).astimezone(KST)
        day = now.date().isoformat()
        target = next_session("XKRX", now) if preview else session("XKRX", day)
        if target is None:
            raise ValueError("한국 휴장일: 아침 가설을 생성하지 않습니다.")
        if not preview and now >= target.open:
            raise ValueError(
                "개장 후에는 아침 원문을 새로 생성하지 않습니다. 저장된 /오늘브리핑을 확인하세요."
            )
        day = target.date
        existing = self.journal.get(f"morning:{day}")
        if existing and not preview:
            return existing
        closed = latest_closed("XNYS", now)
        previous = self.journal.latest("morning")
        repeated = bool(
            previous
            and previous["payload"].get("moves")
            and previous["payload"].get("us_session") == closed.date
        )
        universe = tuple(
            sorted(set(self.database.watchlist()) | set(EQUITIES) | set(LABELS))
        )
        history = {}
        us = {}
        events = []
        news = []
        cards = []
        withheld = []
        if not repeated:
            try:
                history = self.data.regular_history(universe, closed, count=26)
                us = moves(history, closed)
            except (MarketDataError, ValueError) as exc:
                self.data.warnings.append(
                    f"미국 정규장 시세 수집 실패: {type(exc).__name__}; 가격 제안 보류"
                )
            previous_us = latest_closed("XNYS", closed.open)
            news = self.data.news(universe, previous_us.close, closed.close)
            if now > closed.close:
                news += self.data.news(universe, closed.close, now)
            events = self.data.official_events(previous_us.close, now)
        hot = sorted(
            (p for s, p in us.items() if s not in LABELS),
            key=lambda p: abs(p["change"]),
            reverse=True,
        )[:5]
        if not repeated:
            valid = next_session("XNYS", now)
            current_us = session("XNYS", str(now.astimezone(NY).date()))
            trading_now = bool(current_us and current_us.open <= now < current_us.close)
            for p in hot:
                symbol = p["symbol"]
                if trading_now:
                    withheld.append(
                        f"{symbol}: 미국 정규장 진행 중; 이전 종가 기반 새 가격안 보류"
                    )
                    continue
                if symbol not in EQUITIES:
                    withheld.append(f"{symbol}: 현금 보통주 여부 미확인")
                    continue
                name, sector = EQUITIES[symbol]
                card, reason = make_card(
                    symbol,
                    name,
                    sector,
                    history[symbol],
                    market="US",
                    session_date=closed.date,
                    valid_session=valid,
                    feed="sip",
                    profile=self.profile,
                    now=now,
                )
                if card and len(cards) < 3:
                    cards.append(card)
                elif reason:
                    withheld.append(f"{symbol}: {reason}")
        allocate(cards, self.profile, now)
        hs = hypotheses(us, day) if not repeated else []
        prior = self.journal.latest("close")
        if prior and (
            prior["trade_date"] >= day or datetime.fromisoformat(prior["asof"]) > now
        ):
            prior = None
        payload = dict(
            us_session=closed.date,
            us_open=closed.open.isoformat(),
            us_close=closed.close.isoformat(),
            target_open=target.open.isoformat(),
            target_close=target.close.isoformat(),
            repeated_us=repeated,
            moves=us,
            history=history,
            news=news,
            events=events,
            hypotheses=hs,
            cards=cards,
            withheld=withheld,
            warnings=self.data.warnings,
            previous_close_id=prior["id"] if prior else None,
            profile_missing=self.profile.missing(),
            universe=universe,
            mode="preview" if preview else "live",
        )
        body = render_morning(day, now, payload, hot, prior, preview)
        edition = self.journal.save("morning", day, now, body, payload, preview=preview)
        if not preview:
            self.database.save_report(day, edition["body"])
        return edition

    def close(self, now=None, *, preview=False):
        now = (now or datetime.now(KST)).astimezone(KST)
        day = str(now.date())
        target = session("XKRX", day)
        if not target:
            raise ValueError("한국 휴장일: 마감 복기를 생성하지 않습니다.")
        if now < target.close + timedelta(minutes=5):
            raise ValueError("정규장 마감 5분 뒤부터 확인합니다.")
        existing = self.journal.get(f"close:{day}")
        if existing and not preview:
            return existing
        domestic = self.data.domestic(now, expected_day=day)
        morning = self.journal.get(f"morning:{day}")
        # A current preview cannot be passed off as a historical morning prediction.
        if morning and datetime.fromisoformat(morning["asof"]) >= target.open:
            morning = None
        reviews = (
            [
                review_hypothesis(h, domestic["indices"])
                for h in morning["payload"]["hypotheses"]
            ]
            if morning
            else []
        )
        card_reviews = []
        source_editions = self.journal.recent_mornings(now)
        for source_edition in source_editions:
            for card in source_edition["payload"]["cards"]:
                if self.journal.subject_status(card["id"]) in {"폐기·재분석", "무효화"}:
                    continue
                bar = None
                if now >= datetime.fromisoformat(card["valid_until"]) + timedelta(
                    minutes=20
                ):
                    try:
                        end = latest_closed("XNYS", now)
                        history = self.data.regular_history(
                            [card["symbol"]], end, count=2
                        )
                        target_day = str(
                            datetime.fromisoformat(card["valid_from"])
                            .astimezone(NY)
                            .date()
                        )
                        bar = next(
                            (
                                b
                                for b in history.get(card["symbol"], [])
                                if b["date"] == target_day
                            ),
                            None,
                        )
                    except (MarketDataError, ValueError):
                        pass
                card_reviews.append(
                    dict(
                        id=card["id"],
                        source_id=source_edition["id"],
                        **review_card(card, bar, now),
                    )
                )
        payload = dict(
            domestic=domestic,
            morning_id=morning["id"] if morning else None,
            hypothesis_reviews=reviews,
            card_reviews=card_reviews,
            warnings=self.data.warnings,
            mode="preview" if preview else "live",
        )
        body = render_close(day, now, payload, morning, preview)
        edition = self.journal.save("close", day, now, body, payload, preview=preview)
        if not preview:
            for review in reviews:
                self.journal.event(
                    edition["id"], review["id"], review["status"], review["reason"]
                )
            for review in card_reviews:
                self.journal.event(
                    edition["id"], review["id"], review["decision"], review["reason"]
                )
        return edition


def render_morning(day, now, p, hot, prior, preview):
    us = p["moves"]
    lines = [f"🇺🇸🇰🇷 **아침 주식 브리핑 · 한국 거래일 {day}**"]
    if preview:
        lines.append(
            "**샘플·장전 형식 미리보기: 지금 확인 가능한 자료만 사용. 실제 아침 발송본/과거 예측이 아님.**"
        )
    lines += [
        f"확인 시각 {now:%Y-%m-%d %H:%M %Z} · 미국 정규장 {p['us_session']} ({p['us_open']}~{p['us_close']})",
        "",
        "**핵심 3줄**",
    ]
    if p["repeated_us"]:
        lines += [
            "1. 새로 종료된 미국 정규장이 없어 이전 수치·뉴스를 반복하지 않습니다.",
            "2. 국내 새 가설과 신규 가격안 생성은 보류합니다.",
            "3. 한국장 마감에는 확인된 국내 자료와 기존 원문을 검토합니다.",
        ]
    else:
        benchmark = (
            " · ".join(
                f"{LABELS[s]} {us[s]['change']:+.2f}%"
                for s in ("SPY", "QQQ", "SMH")
                if s in us
            )
            or "정규장 지수 대용 ETF 자료 부족"
        )
        lines += [
            f"1. [사실] {benchmark}.",
            f"2. [사실] 확인 범위 내 최대 변동: {hot[0]['symbol']} {hot[0]['change']:+.2f}%. 원인은 아래 근거와 구분합니다."
            if hot
            else "2. 종목별 움직임을 비교할 자료가 부족합니다.",
            f"3. 관찰 가설 {len(p['hypotheses'])}개, 가격 검토 카드 {len(p['cards'])}개. 검증되지 않은 인과관계는 추론으로만 다룹니다.",
        ]
    lines += [
        "",
        "**미국 주요 움직임 · 최대 5개**",
        "전체 시장 순위가 아니라 고정 관심종목+금융·소비·에너지·헬스케어 등 관찰군 내 선별입니다.",
    ]
    for item in hot:
        symbol = item["symbol"]
        title = EQUITIES.get(symbol, (symbol, ""))[0]
        lines.append(
            f"• [사실] **{title}({symbol}) {item['change']:+.2f}%**, 정규장 마지막 봉 종가 ${item['close']:,.2f}, 거래량 {item['volume']:,.0f}주(이전 20세션 평균의 {item['volume_ratio']:.2f}배)."
        )
        associated = [n for n in p["news"] if symbol in n["symbols"] and n["url"]]
        before = [
            n
            for n in associated
            if datetime.fromisoformat(n["at"]) <= datetime.fromisoformat(p["us_close"])
        ]
        after = [
            n
            for n in associated
            if datetime.fromisoformat(n["at"]) > datetime.fromisoformat(p["us_close"])
        ]
        if before:
            n = before[0]
            lines.append(
                f"  [외부 보도] [{n['title'][:110]}]({n['url']}) · {n['at']} · {n['source']}."
            )
            lines.append(
                "  "
                + event_path(n["title"])
                + " "
                + event_price_order(n, item, p["us_open"])
            )
        else:
            lines.append(
                "  개별 실적·공시 원인은 확인되지 않았습니다. 거래 증가와 원인 확인을 구분합니다."
            )
        if after:
            n = after[0]
            lines.append(
                f"  [마감 후 보도] [{n['title'][:85]}]({n['url']}) · {n['at']}. 해당 정규장 등락의 원인으로 사용하지 않습니다."
            )
    lines += [
        "시간외 가격 변화는 이번 자료에서 수집하지 않았습니다. 정규장 수익률과 합산하지 않습니다.",
        "",
        "**공식 발표 확인**",
    ]
    for e in p["events"][:3]:
        lines.append(
            f"• [1차 자료] [{e['title'][:100]}]({e['url']}) · {e['at']} · {e['source']}. 시장 영향은 별도 검증이 필요합니다."
        )
    if not p["events"]:
        lines.append(
            "이번 확인 구간에 연결할 수 있는 공식 발표를 확보하지 못했습니다. 부재가 사건이 없었다는 뜻은 아닙니다."
        )
    lines += ["", "**한국 시장 전달 경로**"]
    smh = us.get("SMH")
    energy = us.get("XLE")
    finance = us.get("XLF")
    if smh and abs(smh["change"]) >= 0.5:
        lines.append(
            f"• [봇 추론] 미국 반도체 ETF {smh['change']:+.2f}% → 반도체 수요·설비투자 기대와 위험선호 → 한국 메모리 업종의 관심 변화 가능성. 삼성전자·SK하이닉스의 메모리 사업은 공식 자료로 확인되지만, 이번 움직임과 특정 고객 공급계약의 직접 연결은 확인하지 못했습니다."
        )
        lines.append(
            "  사업 노출 근거: [삼성전자 HBM](https://semiconductor.samsung.com/dram/hbm/), [SK하이닉스 회사 소개](https://www.skhynix.com/company/UI-FR-CP02/). 이는 당일 사건이 아닌 사업 배경 자료입니다."
        )
    if energy and abs(energy["change"]) >= 0.8:
        lines.append(
            f"• [봇 추론] 미국 에너지 ETF {energy['change']:+.2f}% → 에너지 업종 투자심리 변화. 유가 자체·정제마진 자료가 없어 국내 정유사 이익 방향이나 항공사 비용 영향을 단정하지 않습니다."
        )
    if finance and abs(finance["change"]) >= 0.8:
        lines.append(
            f"• [봇 추론] 미국 금융 ETF {finance['change']:+.2f}% → 금융업 위험선호 관찰. 한국 은행은 금리·대출규제·대손비용이 달라 개별 종목 연결은 보류합니다."
        )
    if not any(x and abs(x["change"]) >= 0.5 for x in (smh, energy, finance)):
        lines.append(
            "뚜렷한 업종 전달 신호가 부족해 개별 국내 기업을 연결하지 않습니다."
        )
    lines.append(
        "반대 근거: 한국에서 이미 반영된 기대, 원화·정책·개별 공시, 업종 구성 차이가 미국 신호를 압도할 수 있습니다. 국내 당일 공시·환율·수급은 이번 수집 범위에서 확인하지 못했습니다."
    )
    if prior:
        indices = prior["payload"]["domestic"]["indices"]
        lines.append(
            f"직전 국내 마감 {prior['trade_date']}: "
            + " · ".join(f"{v['name']} {v['change']:+.2f}%" for v in indices.values())
            + ". 같은 방향이었다면 선반영 가능성을 점검합니다."
        )
    else:
        lines.append(
            "연결 가능한 직전 국내장 새 형식 원문이 아직 없습니다. 예전 요약을 정규장 확정 자료로 재해석하지 않습니다."
        )
    lines += ["", "**오늘의 관찰 가설**"]
    for h in p["hypotheses"]:
        lines.append(
            f"• {h['id']} — {h['expected']}. {h['reason']}\n  무효/유보 조건: {h['invalidation']}. 마감 시 동일 기준으로 모두 복기합니다."
        )
    if not p["hypotheses"]:
        lines.append("측정 가능한 새 가설을 만들 근거가 부족하여 관망합니다.")
    lines += ["", "**매매 검토 카드**"]
    if not p["cards"]:
        lines.append("**오늘은 신규 매수 제안 없음.**")
    for card in p["cards"]:
        lines.extend(render_card(card))
    if p["withheld"]:
        lines.append("보류 이유: " + " / ".join(p["withheld"]))
    if p["profile_missing"]:
        lines.append(
            "투자 설정 미확인: 증권사·투자금·위험예산·보유/주문·비용 등. 참고용 가정이며 개인화 수량은 산출하지 않습니다."
        )
    lines += [
        "",
        "**자료 범위와 갱신**",
        f"[Alpaca 과거 SIP 시세](https://docs.alpaca.markets/us/reference/stockbars): 미 동부 정규장 안의 5분봉을 집계, 원시가격(raw), {p['us_session']} 마감 기준. 실시간 호가가 아닙니다. 분봉 마지막 가격과 공식 경매 종가는 차이가 있을 수 있습니다.",
        "한국 거래일 08:00 및 정규장 종료 5분 후(통상 15:35)에만 갱신합니다. 지속 가격·공시 감시는 없습니다. 새 무효화 정보는 다음 갱신 또는 명시적 상태 기록에서 반영하며 주문을 변경하지 않습니다.",
    ]
    if p["warnings"]:
        lines.append("수집 상태: " + " / ".join(dict.fromkeys(p["warnings"])))
    return "\n".join(lines)


def render_card(c):
    buy_line = (
        f"보유 관리: 신고한 {c['holding']['quantity']}주 기준의 관리 시나리오. 신규·추가 매수 제안 없음. 관리 손익비는 최근가 기준."
        if c["holding"]
        else f"매수 검토 구간 {c['entry_low']:,.2f}~{c['entry']:,.2f}, 지정가 후보 {c['entry']:,.2f}. {c['entry_condition']}."
    )
    return [
        "",
        f"**{c['name']}({c['symbol']}) · {c['market']} · {c['currency']} — {c['judgment']}**",
        f"기준: {c['reference']:,.2f} · {c['reference_at']} 정규장 마지막 봉 · 지연된 과거 시세 / 기간: {c['horizon']}",
        buy_line,
        f"익절: 1차 {c['target1']:,.2f}(50%), 2차 {c['target2']:,.2f}(50%). 보유 확인 후 매도 검토. 보유 주식 없이 익절/손절을 독립 예약하지 않음.",
        f"손절: 무효·관찰 발동 가격 {c['stop_trigger']:,.2f}; 실제 주문 가격 미지정. {c['stop_method']}.",
        f"손익비: ({c['target1']:,.2f}−{c['entry']:,.2f})÷({c['entry']:,.2f}−{c['stop_trigger']:,.2f})={c['rr1']:.2f}, 2차 {c['rr2']:.2f}. 성공 확률·수익 보장이 아닙니다.",
        f"수량: {str(c['quantity']) + '주(조건부 계산)' if c['quantity'] is not None else '미산출(투자/보유/주문 설정 확인 필요)'}. {c['costs']}.",
        f"유효기간: {c['valid_from']}~{c['valid_until']} 대상 미국 정규장. 취소·재분석: {c['cancellation']}.",
        f"근거: 최근 20세션에서 실제 관측한 국소 저점 {c['support']:,.2f}, 국소 고점 {c['resistance'][0]:,.2f}/{c['resistance'][1]:,.2f}, 14일 ATR {c['atr14']:.4f}. 지지+0.10 ATR를 진입 상한, 지지−0.25 ATR를 무효 가격으로 잡고 호가 단위에 맞춰 내림. 관측 저항을 목표로 사용.",
        f"[원시 정규장 봉 출처]({c['source']}) · [호가 규칙]({RULE_SOURCES['US']}). {c['order_method']}.",
        "갭으로 손절보다 불리하게 체결되거나 지정가가 미체결될 수 있습니다. 익절·손절이 동시에 남는 경우 보유 수량과 서로의 취소 상태를 확인해야 합니다. 연계 주문 지원·유효기간·세션은 증권사 확인 전 미확정입니다.",
    ]


def render_close(day, now, p, morning, preview):
    d = p["domestic"]
    indices = d["indices"]
    lines = [f"🇰🇷 **국내 주식 마감 브리핑 · {day}**"]
    if preview:
        lines.append(
            "**실제 데이터 샘플 · 확인 시점의 마감 자료. 당시 아침 예측을 사후 생성하지 않았습니다.**"
        )
    summary = (
        " · ".join(
            f"{v['name']} {v['close']:,.2f}({v['change']:+.2f}%)"
            for v in indices.values()
        )
        or "당일 확정 지수 자료 미확보"
    )
    lines += [
        f"확인 시각 {now:%Y-%m-%d %H:%M %Z}",
        "",
        "**핵심 3줄**",
        f"1. [사실] {summary}.",
        f"2. 보존된 당일 아침 원문 {'있음: ' + morning['id'] if morning else '없음: 아침 가설·가격 성과 판단 유보'}.",
        "3. 종목 움직임의 원인과 실제 체결은 별도 확인이 필요합니다. 오늘 관측 결과로 인과관계나 수익률을 확정하지 않습니다.",
        "",
        "**오늘의 시장**",
    ]
    for v in indices.values():
        lines.append(
            f"• [{v['name']}]({v['source']}) {v['close']:,.2f}, {v['change']:+.2f}% · {v['at']} · {v['session']} · {v['delay']}."
        )
    if len(indices) == 2:
        spread = indices["KOSDAQ"]["change"] - indices["KOSPI"]["change"]
        lines.append(
            f"[관찰] 코스닥−코스피 수익률 차이 {spread:+.2f}%p. 중소형/성장주 선호와 관련될 수 있지만 지수 구성과 개별 대형주 영향도 존재합니다."
        )
    lines += [
        "업종지수·외국인/기관 순매수와 확정/잠정 수급 자료는 확보하지 못했습니다. 지수 등락만으로 수급 주체나 정책·실적 원인을 추정하지 않습니다.",
        "",
        "**주목할 종목 · 현재 공개 순위 보조자료**",
        "아래 종목값은 장후·대체거래소 혼합 여부를 검증하지 못했습니다. 정규장 종가·매매 가격안·가설 성과 판정에서 제외합니다.",
    ]
    for stock in d["stocks"][:5]:
        lines.append(
            f"• [{stock['name']}({stock['symbol']})]({stock['source']}) 관측가 {stock['close']:,.0f}원, 제공처 등락 {stock['change']:+.2f}%, 거래대금 {stock['value'] / 1e8:,.1f}억원 · 수집 {d['asof']}. 기업 고유 공시 원인 미확인; 업종 공통 요인과 구분할 근거 부족."
        )
    if not d["stocks"]:
        lines.append("확인 가능한 당일 종목 순위 없음.")
    lines += ["", "**아침 가설 복기 · 원문 보존**"]
    if not morning:
        lines.append(
            "이 날짜에 개장 전에 보존한 가설이 없습니다. 사후 정보를 이용해 아침 가설이나 적중 결과를 만들지 않습니다."
        )
    for h in p["hypothesis_reviews"]:
        lines.append(f"• {h['id']}: **{h['status']}** — {h['reason']}")
    if morning and not p["hypothesis_reviews"]:
        lines.append("아침 원문에서 새 가설을 제시하지 않아 평가 대상이 없습니다.")
    lines += ["", "**가격 시나리오 점검**"]
    for c in p["card_reviews"]:
        lines.append(
            f"• {c['id']}: **{c['status']} / {c['decision']}** — {c['reason']}"
        )
    if not p["card_reviews"]:
        lines.append(
            "당일 아침에 저장된 가격안이 없어 신규 체결·수익을 추정하지 않습니다."
        )
    lines += [
        "실제 주문·체결 데이터는 수집하지 않습니다. 가격 도달은 가상 관찰이며, 추가 진입 조건 충족·체결 여부와 다릅니다. 다음 거래일에는 최신 자료로 새 버전을 만들고 오늘 원문과 상태 이력은 보존합니다.",
        "",
        "**오늘 배운 점**",
        "지수의 방향, 개별 기업의 원인, 주문의 체결은 서로 다른 증거가 필요합니다. 특히 장후 가격을 정규장 종가처럼 비교하면 오전 판단의 성과를 잘못 평가할 수 있습니다. 이 하루 사례를 일반적인 매매 법칙으로 확대하지 않습니다.",
        "",
        "**갱신 안내**",
        "08:00 아침 / 한국 정규장 종료 5분 후 마감 갱신. 지속 감시 없음. 거래소 달력 밖 임시 휴장·정정 가능성은 시세 날짜·마감 상태로 추가 점검합니다.",
    ]
    if p["warnings"]:
        lines.append("수집 상태: " + " / ".join(dict.fromkeys(p["warnings"])))
    return "\n".join(lines)
