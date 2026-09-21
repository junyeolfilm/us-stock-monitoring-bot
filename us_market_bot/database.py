from __future__ import annotations

import sqlite3
from contextlib import contextmanager
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from us_market_bot.config import DEFAULT_WATCHLIST


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    report_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domestic_reports (
    market_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    body TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
"""


class MarketDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            count = int(
                connection.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            )
            if count == 0:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                connection.executemany(
                    "INSERT INTO watchlist(symbol, added_at) VALUES (?, ?)",
                    ((symbol, now) for symbol in DEFAULT_WATCHLIST),
                )

    def watchlist(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol FROM watchlist ORDER BY symbol"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def add_symbol(self, symbol: str) -> bool:
        normalized = normalize_symbol(symbol)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?, ?)",
                (normalized, datetime.now().astimezone().isoformat(timespec="seconds")),
            )
        return cursor.rowcount > 0

    def remove_symbol(self, symbol: str) -> bool:
        normalized = normalize_symbol(symbol)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM watchlist WHERE symbol = ?", (normalized,)
            )
        return cursor.rowcount > 0

    def get_state(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row[0])

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def save_report(self, report_date: str, body: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reports(report_date, generated_at, body) VALUES (?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    body = excluded.body
                """,
                (
                    report_date,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    body,
                ),
            )

    def latest_report(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM reports ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
        return None if row is None else str(row[0])

    def save_domestic_report(self, snapshot, body: str) -> None:
        payload = json.dumps(
            asdict(snapshot),
            ensure_ascii=False,
            default=lambda value: (
                value.isoformat() if isinstance(value, datetime) else str(value)
            ),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO domestic_reports(
                    market_date, generated_at, body, snapshot_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(market_date) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    body = excluded.body,
                    snapshot_json = excluded.snapshot_json
                """,
                (
                    snapshot.market_date,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    body,
                    payload,
                ),
            )

    def latest_domestic_report(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM domestic_reports ORDER BY market_date DESC LIMIT 1"
            ).fetchone()
        return None if row is None else str(row[0])

    def latest_domestic_snapshot(self):
        from us_market_bot.models import (
            DomesticIndexMove,
            DomesticSnapshot,
            DomesticStockMove,
        )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json
                FROM domestic_reports
                ORDER BY market_date DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        return DomesticSnapshot(
            market_date=str(payload["market_date"]),
            collected_at=datetime.fromisoformat(str(payload["collected_at"])),
            indices=tuple(DomesticIndexMove(**item) for item in payload["indices"]),
            gainers=tuple(DomesticStockMove(**item) for item in payload["gainers"]),
            losers=tuple(DomesticStockMove(**item) for item in payload["losers"]),
            value_leaders=tuple(
                DomesticStockMove(**item) for item in payload["value_leaders"]
            ),
        )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol or len(symbol) > 12:
        raise ValueError("종목 코드는 1~12자로 입력하세요.")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
    if any(character not in allowed for character in symbol):
        raise ValueError("영문 종목 코드만 입력하세요.")
    return symbol
