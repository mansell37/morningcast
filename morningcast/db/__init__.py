"""Lightweight SQLite persistence.

Deliberately thin and dependency-free so it runs locally now and can be swapped
for Postgres on Railway later (the schema is portable; only the connection changes).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH
from ..models import Episode, Topic, TopicSource, TopicStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefings (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    markdown TEXT NOT NULL,
    sources TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    audio_path TEXT NOT NULL,
    duration_seconds INTEGER DEFAULT 0,
    rating REAL,
    published_at TEXT NOT NULL
);
"""


def _conn(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DB_PATH) -> None:
    with _conn(path) as c:
        c.executescript(SCHEMA)


# --- topics ----------------------------------------------------------------

def save_topic(topic: Topic) -> Topic:
    topic.updated_at = datetime.now(timezone.utc)
    with _conn() as c:
        c.execute(
            """INSERT INTO topics (id, title, source, status, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, source=excluded.source, status=excluded.status,
                 notes=excluded.notes, updated_at=excluded.updated_at""",
            (
                topic.id, topic.title, topic.source.value, topic.status.value,
                topic.notes, topic.created_at.isoformat(), topic.updated_at.isoformat(),
            ),
        )
    return topic


def get_topics(status: TopicStatus | None = None) -> list[Topic]:
    q = "SELECT * FROM topics"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status.value,)
    q += " ORDER BY created_at DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_topic(r) for r in rows]


def get_topic(topic_id: str) -> Topic | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    return _row_to_topic(r) if r else None


def _row_to_topic(r: sqlite3.Row) -> Topic:
    return Topic(
        id=r["id"], title=r["title"], source=TopicSource(r["source"]),
        status=TopicStatus(r["status"]), notes=r["notes"],
        created_at=datetime.fromisoformat(r["created_at"]),
        updated_at=datetime.fromisoformat(r["updated_at"]),
    )


# --- episodes --------------------------------------------------------------

def save_episode(ep: Episode) -> Episode:
    with _conn() as c:
        c.execute(
            """INSERT INTO episodes
               (id, topic_id, title, summary, audio_path, duration_seconds, rating, published_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary, audio_path=excluded.audio_path,
                 duration_seconds=excluded.duration_seconds, rating=excluded.rating""",
            (
                ep.id, ep.topic_id, ep.title, ep.summary, ep.audio_path,
                ep.duration_seconds, ep.rating, ep.published_at.isoformat(),
            ),
        )
    return ep


def get_episodes() -> list[Episode]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM episodes ORDER BY published_at DESC").fetchall()
    return [
        Episode(
            id=r["id"], topic_id=r["topic_id"], title=r["title"], summary=r["summary"],
            audio_path=r["audio_path"], duration_seconds=r["duration_seconds"],
            rating=r["rating"], published_at=datetime.fromisoformat(r["published_at"]),
        )
        for r in rows
    ]
