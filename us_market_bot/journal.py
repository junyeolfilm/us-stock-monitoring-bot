"""Append-only research ledger and conservative at-most-once delivery claims."""

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_editions (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, trade_date TEXT NOT NULL,
 asof TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_events (
 id INTEGER PRIMARY KEY, edition_id TEXT NOT NULL, created_at TEXT NOT NULL,
 subject_id TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefing_delivery (
 edition_id TEXT NOT NULL, chunk INTEGER NOT NULL, status TEXT NOT NULL,
 claimed_at TEXT NOT NULL, message_id TEXT, PRIMARY KEY(edition_id, chunk)
);
CREATE TRIGGER IF NOT EXISTS immutable_editions_update BEFORE UPDATE ON research_editions
 BEGIN SELECT RAISE(ABORT, 'research editions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_editions_delete BEFORE DELETE ON research_editions
 BEGIN SELECT RAISE(ABORT, 'research editions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON research_events
 BEGIN SELECT RAISE(ABORT, 'research events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON research_events
 BEGIN SELECT RAISE(ABORT, 'research events are immutable'); END;
"""


class Journal:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as c:
            c.executescript(SCHEMA)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, kind, day, asof, body, payload, *, preview=False):
        edition_id = f"{kind}:{day}"
        if preview:
            edition_id = f"preview:{edition_id}:{hashlib.sha256(asof.isoformat().encode()).hexdigest()[:12]}"
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO research_editions VALUES (?,?,?,?,?,?,?)",
                (
                    edition_id,
                    kind,
                    day,
                    asof.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    body,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        return self.get(edition_id)

    def get(self, edition_id):
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM research_editions WHERE id=?", (edition_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def latest(self, kind):
        with self.connect() as c:
            row = c.execute(
                "SELECT id FROM research_editions WHERE kind=? AND id NOT LIKE 'preview:%' ORDER BY trade_date DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return self.get(row[0]) if row else None

    def event(self, edition_id, subject_id, status, detail):
        with self.connect() as c:
            c.execute(
                "INSERT INTO research_events(edition_id, created_at, subject_id, status, detail) VALUES(?,?,?,?,?)",
                (
                    edition_id,
                    datetime.now(timezone.utc).isoformat(),
                    subject_id,
                    status,
                    detail,
                ),
            )

    def claim(self, edition_id, chunk):
        with self.connect() as c:
            cursor = c.execute(
                "INSERT OR IGNORE INTO briefing_delivery(edition_id,chunk,status,claimed_at) VALUES(?,?,'claimed',?)",
                (edition_id, chunk, datetime.now(timezone.utc).isoformat()),
            )
        return cursor.rowcount == 1

    def delivery_state(self, edition_id, chunk):
        with self.connect() as c:
            row = c.execute(
                "SELECT status FROM briefing_delivery WHERE edition_id=? AND chunk=?",
                (edition_id, chunk),
            ).fetchone()
        return row[0] if row else None

    def delivered(self, edition_id, chunk, message_id):
        with self.connect() as c:
            c.execute(
                "UPDATE briefing_delivery SET status='sent',message_id=? WHERE edition_id=? AND chunk=?",
                (str(message_id), edition_id, chunk),
            )

    def unresolved(self):
        with self.connect() as c:
            return c.execute(
                "SELECT count(*) FROM briefing_delivery WHERE status='claimed'"
            ).fetchone()[0]

    def recent_mornings(self, before):
        with self.connect() as c:
            rows = c.execute(
                "SELECT id FROM research_editions WHERE kind='morning' AND id NOT LIKE 'preview:%' AND asof<? ORDER BY trade_date DESC LIMIT 10",
                (before.isoformat(),),
            ).fetchall()
        return [self.get(row[0]) for row in rows]

    def subject_status(self, subject_id):
        with self.connect() as c:
            row = c.execute(
                "SELECT status FROM research_events WHERE subject_id=? ORDER BY id DESC LIMIT 1",
                (subject_id,),
            ).fetchone()
        return row[0] if row else None
