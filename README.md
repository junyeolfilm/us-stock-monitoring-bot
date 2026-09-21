# 한미 주식 브리핑 봇

아침 신문처럼 읽는 개인용 시장 분석 도구입니다. 기존 Discord 채널을 사용하며 분석·참고 가격안만 제공합니다. 주문 생성·제출·수정·취소, 자동매매, 블로그 발행 기능은 없습니다.

## 주요 기능

- 한국 거래일 08:00: 핵심 3줄, 최근 완료된 미국 정규장 3~5개 움직임, 국내 전달 경로, 최대 3개 관찰 가설, 조건을 충족한 최대 3개 가격 카드.
- 한국 정규장 종료 5분 후(통상 15:35): 국내 지수·종목 보조자료, 아침의 모든 가설 복기, 가격안 검토, 배운 점.
- 미국 NYSE/한국 KRX 거래소 달력(`exchange-calendars` 고정 버전): 휴장·서머타임·조기 폐장 반영. 한국 휴장일에는 생성하지 않고 미국 정규장이 새로 종료되지 않았으면 수치·뉴스를 새 소식으로 재발송하지 않습니다.
- 개장 후 아침 가설 생성 금지. 오프라인이었다가 복구되면 아침은 개장 전까지만, 마감 자동 발송은 종료 후 2시간 이내만 시도합니다.
- 매일 본문·근거·가설·가격 원문을 `research_editions`에 불변 보관하고 상태 변화는 `research_events`에 추가합니다. 기존 DB 테이블은 보존됩니다.
- 발송 전 `briefing_delivery`에 원자적 claim. 성공한 조각만 건너뛰며 응답 미확정 시 자동 재전송을 보류합니다(`/상태`). 이는 중복을 피하는 at-most-once 정책으로, 네트워크 장애 시 일부 미발송 가능성이 있습니다.
- 기존 명령 유지 및 `/가격안무효 가격안id 이유` 추가: 원문을 보존하고 상태를 기록해 같은 채널에 알립니다. 실제 주문은 바꾸지 않습니다.

## 판단 원칙

현재 운영 경로는 `ResearchService`입니다. 기존 `report.py`/`domestic.py`의 가중 신뢰도·키워드 연결 방식은 호환 테스트용으로 남아 있고 새 브리핑에는 사용하지 않습니다. 고정 관심종목과 금융·소비·에너지·헬스케어·방산 등 명시된 관찰군에서 변동을 선정하며 전체 시장 스캔이라고 표현하지 않습니다.

사실·외부 보도·봇 추론을 분리합니다. 뉴스 게시/수정 시각을 확인 시각으로 잘라내고 정규장 마감 후 기사를 해당 장의 원인으로 사용하지 않습니다. 확보된 분봉으로 기사 전후 움직임을 비교하되 인과를 입증했다고 표현하지 않습니다. 공시 원문을 확보하지 못하면 기업 고유 원인은 미확인으로 남깁니다. 외부 기사 내용은 텍스트로만 처리하며 지시문을 실행하지 않습니다.

가설은 코스피 방향·코스닥의 코스피 대비 수익률처럼 확보 가능한 지수로 검증할 수 있는 조건만 기록합니다. 마감 때 모든 가설을 부합/일부 부합/불일치/판단 유보로 검토합니다. 업종지수 미확보 사실도 표시합니다. 가격 도달은 가상 관찰이며 실제 체결과 다릅니다. 익절·손절을 모두 지난 날은 순서를 모르면 판단 유보합니다.

지속 가격·뉴스 감시는 없습니다. 갱신은 위 두 일정과 수동 명령뿐입니다. 미국 가격안은 미국 유효 세션을 명시하고 그 세션이 끝난 다음 국내 마감 갱신에서 검토합니다. 새 사실을 확인하면 `/가격안무효`로 즉시 기록할 수 있습니다.

## 데이터

- 미국: [Alpaca 과거 bars](https://docs.alpaca.markets/us/reference/stockbars), `feed=sip`, `adjustment=raw`, 5분봉 중 NYSE 정규장만 집계. 26개 완료 세션을 요청하며 시간외는 제외합니다. IEX를 시장 전체 거래량으로 간주하지 않습니다. 현재 계정의 과거 SIP 접근을 확인했고 새 유료 구독은 만들지 않았습니다.
- 뉴스: Alpaca/Benzinga(외부 보도), 연준·BLS 공식 RSS 우선. 기사 전체 해석/모든 기업 IR·공시 자동 검증까지 제공하는 것은 아닙니다. SEC 접근은 현재 환경에서 거부되었고 우회하지 않습니다.
- 국내: 네이버 지수의 날짜·마감 상태 확인. 종목 순위에는 장후/NXT 혼합 가능성이 있어 세션 미확인 보조자료로 표시하고 가격 제안·가설 판정에서 제외합니다.
- 국내 수급·업종지수·개별 공시·환율은 현재 미확보입니다. 정규장/원시가격 검증 가능한 국내 데이터(예: 사용 증권사의 읽기 전용 시세)가 연결될 때까지 한국 종목의 정밀 가격 제안은 보류합니다.

분봉 마지막 가격은 공식 마감 경매 가격과 다를 수 있습니다. API 응답 지연·정정과 거래소 달력에 아직 반영되지 않은 임시 휴장은 남은 한계입니다. 수집 실패 시 확인한 정보만 싣고 가격안을 보류합니다. 새 유료 데이터가 필요하면 별도 비용 확인 후 선택해야 합니다.

## 가격과 투자 설정

`investment_profile.example.json`을 개인용 `investment_profile.json`으로 복사합니다(Git 제외). 이 프로젝트 전용 시장·증권사·통화·가용 자금·종목 한도·손실 예산·업종 합산 한도·보유 기간·보유/주문 목록·기준 시각을 입력해야 합니다. `null`은 미확인, 빈 목록은 없음 확인입니다. 다른 프로젝트 설정은 가져오지 않습니다.

현재 참고 가격은 검증된 미국 현금 보통주에 한합니다. 최소 25개 연속 정규장 전체 5분봉과 거래량, 최근 20세션 국소 저점/고점, 14일 ATR을 사용합니다. 지지+0.10 ATR(현재 기준가 이하)을 진입 상한, 지지−0.25 ATR을 무효 가격으로 삼고 실제 관측한 위쪽 저항 2개를 목표로 사용합니다. 1차 가격 손익비 1.5 미만·저항 부족·큰 권리변동 가능성·봉 누락이면 보류합니다. 신뢰도나 성공 확률은 생성하지 않습니다. 이 기준은 검증된 초과수익 전략이라는 뜻이 아닙니다.

수량은 최신(24시간 이내) 계좌 정보와 비용·증권사 공식 안내 검증(30일 이내)이 모두 있어야 계산합니다. `costs`는 `buy_rate`, `sell_rate`(세금 포함), `slippage_per_share`, `fx_cost_per_share`를 같은 통화로 지정합니다. `broker_rules`에는 `broker`, `official_url`, `verified_at`, `cash_equity_only`가 필요합니다. 보유 목록은 `symbol`, `market`, `quantity`, `average_cost`, `market_value`, `sector`를 포함합니다. 기존 주문이 있거나 평가액·업종이 없으면 합산 위험 계산을 보류합니다.

수량은 비용 포함 주당 위험으로 손실 예산을 나눈 값, 종목 한도, 남은 가용 자금, 같은 업종 잔여 한도 중 최소 정수입니다. 갭 손실을 제한한다고 보장하지 않습니다. 보유 종목은 관리 시나리오이며 평단 회복을 이유로 추가 매수하지 않습니다. 주문 단위는 정수 1주 가정입니다.

증권사를 확인하기 전 조건부 주문·지원 세션·유효기간을 확정하지 않습니다. 돌파가를 보통 매수 지정가로, 손절가를 보통 매도 지정가로 바꾸지 않습니다. 현재는 수동 가격 확인/증권사 알림 후 판단으로 안내하며 독립적인 미보유 익절/손절 예약은 권하지 않습니다. 익절·손절이 함께 남을 때 취소·수량 관리 필요성을 표시합니다.

호가 자료(2026-09-21 확인): [SEC Rule 612 유예 안내](https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126), [KRX 규정](https://regulation.krx.co.kr/contents/RGL/03/03020100/RGL03020100.jsp). 미국 2027-11-01 이후에는 재확인 전 가격 산정을 차단합니다. 한국 호가 계산 함수는 테스트되어 있지만 세션 검증 부족으로 실제 카드에는 사용하지 않습니다.

## 설정

`.env.example`을 `.env`로 복사하고 Discord 및 Alpaca 값을 입력합니다. 인증정보는 Git에 포함되지 않습니다.

```dotenv
DISCORD_BOT_TOKEN=""
DISCORD_CHANNEL_ID=""
DISCORD_BOT_NAME="미국 주식 동향 봇"
ALPACA_API_KEY_ID=""
ALPACA_API_SECRET_KEY=""
ALPACA_DATA_FEED="iex"
US_MARKET_REPORT_HOUR="8"
US_MARKET_REPORT_MINUTE="0"
KR_MARKET_REPORT_HOUR="15"
KR_MARKET_REPORT_MINUTE="35"
US_MARKET_TIMEZONE="Asia/Seoul"
```

## 로컬 확인

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m us_market_bot.discord_bot --check-config
.venv/bin/python -m us_market_bot.cli --once
.venv/bin/python -m us_market_bot.sample --output samples
```

샘플 명령은 `.env`를 읽고 현재 시점의 근거와 아침 형식 미리보기/당일 마감 샘플을 생성합니다. Discord 발송·주문 호출은 없습니다. 실제 아침이 아닌 시점의 미리보기는 운영 가설에 포함하지 않습니다. 과거 오전을 재현하려고 오후 정보를 주입하지 않습니다. 샘플과 증거 JSON은 Git에 포함하지 않습니다.

## Windows 미니PC 설치

PowerShell에서 다음 명령을 실행합니다. 예약 작업 권한이 부족하면 현재 사용자의 시작프로그램으로 자동 등록합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_windows.ps1
```

해제:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_windows.ps1 -Uninstall
```

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 보안

Discord 토큰, Alpaca 키, 향후 KRX·OpenDART 키는 `.env`에만 보관합니다. 실제 인증정보나 데이터베이스는 GitHub에 올리지 않습니다.
