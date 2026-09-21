"""Exchange sessions, never inferred from weekdays or a fixed UTC offset."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import exchange_calendars as xcals


@dataclass(frozen=True)
class Session:
    date: str
    open: datetime
    close: datetime


@lru_cache(maxsize=4)
def calendar(market: str):
    return xcals.get_calendar(market)


def session(market: str, day: str) -> Session | None:
    cal = calendar(market)
    if not cal.is_session(day):
        return None
    return Session(
        day,
        cal.session_open(day).to_pydatetime(),
        cal.session_close(day).to_pydatetime(),
    )


def latest_closed(market: str, now: datetime) -> Session:
    if now.tzinfo is None:
        raise ValueError("기준 시각에는 시간대가 필요합니다.")
    for offset in range(20):
        item = session(market, (now.date() - timedelta(days=offset)).isoformat())
        if item and item.close + timedelta(minutes=20) <= now:
            return item
    raise ValueError("완료된 거래 세션을 확인하지 못했습니다.")


def next_session(market: str, now: datetime) -> Session:
    for offset in range(20):
        item = session(market, (now.date() + timedelta(days=offset)).isoformat())
        if item and item.open > now:
            return item
    raise ValueError("다음 거래 세션을 확인하지 못했습니다.")


def prior_sessions(market: str, day: str, count: int) -> list[Session]:
    cal = calendar(market)
    labels = cal.sessions_window(day, -count)
    return [session(market, str(label.date())) for label in labels]
