# 미국 주식 동향 봇

매일 오전 8시(한국 시간)에 미국 시장 동향, 당일 화제 종목, 국내 증시 영향 관찰 후보를 Discord로 전달하는 독립 봇입니다.

## 주요 기능

- SPY, QQQ, DIA, IWM과 주요 업종 ETF의 전일 등락 정리
- 상승·하락 상위, 거래량 순위, 뉴스 수를 합산한 화제 종목 자동 선별
- 고정 관심 종목과 당일 자동 선별 종목을 함께 분석
- 사전 검토된 업종 연결표를 이용한 국내장 영향 관찰 후보 제시
- 관련 뉴스 원문 링크와 분석 근거 표시
- 매일 오전 8시 자동 브리핑, 미국 휴장·주말에는 최근 거래일 기준 표시
- Discord 명령 `/상태`, `/오늘브리핑`, `/지금브리핑`, `/관심종목`, `/종목추가`, `/종목삭제`, `/분석기준`

## 판단 원칙

화제 종목은 등락률, 거래량 순위, 뉴스 발생량, 관심 종목 여부를 합산해 선정합니다. 국내 종목은 미국 종목이나 업종과 사전에 검토된 연결 규칙이 일치할 때만 표시합니다. 출력은 매수·매도 추천이나 확정적인 가격 예측이 아니라 개장 전 확인할 시장 관찰 목록입니다.

현재 국내 후보는 가격 예측이 아니라 업종 연결을 보여주는 1차 목록입니다. KRX 일별 데이터가 연결되면 미국 전일 수익률과 국내 다음 거래일 수익률의 60일·120일 선행 민감도를 추가할 예정입니다.

## 데이터

- 미국 시세·거래량·뉴스: Alpaca Market Data API
- 미국 기업 공시: SEC EDGAR 연동 예정
- 국내 일별 시세: KRX OPEN API 승인 후 연동 예정
- 국내 기업 공시·사업 관계: OpenDART 인증키 발급 후 연동 예정

무료 Alpaca Basic의 IEX 데이터는 미국 전체 거래소 통합 실시간 시세가 아닙니다. 이 봇은 초단기 매매용이 아니라 장 마감 후 동향 요약용으로 설계합니다.

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
US_MARKET_TIMEZONE="Asia/Seoul"
```

## 로컬 확인

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m us_market_bot.discord_bot --check-config
.venv/bin/python -m us_market_bot.cli --once
```

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

