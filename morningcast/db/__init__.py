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
from ..models import (
    BuildTarget,
    Episode,
    Script,
    ScriptLine,
    Topic,
    TopicSource,
    TopicStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT DEFAULT '',
    last_error TEXT DEFAULT '',
    build_target TEXT DEFAULT 'cloud',
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
CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    lines TEXT NOT NULL,
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
    tags TEXT DEFAULT '',
    audio_backend TEXT DEFAULT '',
    published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Defaults seeded on first init so the UI dropdowns show a sensible state.
_SETTING_DEFAULTS = {
    "style_preset": "dry_british",
    "voice_a": "bm_george",
    "voice_b": "bf_emma",
    "target_minutes": "5",
}


def _conn(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DB_PATH) -> None:
    with _conn(path) as c:
        c.executescript(SCHEMA)
        # Backward-compat: add columns introduced after the initial release.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(topics)").fetchall()}
        if "last_error" not in cols:
            c.execute("ALTER TABLE topics ADD COLUMN last_error TEXT DEFAULT ''")
        if "build_target" not in cols:
            c.execute("ALTER TABLE topics ADD COLUMN build_target TEXT DEFAULT 'cloud'")
        ep_cols = {r["name"] for r in c.execute("PRAGMA table_info(episodes)").fetchall()}
        if "tags" not in ep_cols:
            c.execute("ALTER TABLE episodes ADD COLUMN tags TEXT DEFAULT ''")
        if "audio_backend" not in ep_cols:
            c.execute("ALTER TABLE episodes ADD COLUMN audio_backend TEXT DEFAULT ''")
        for k, v in _SETTING_DEFAULTS.items():
            c.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?,?)", (k, v))


# --- settings --------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _conn() as c:
        r = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# --- topics ----------------------------------------------------------------

def save_topic(topic: Topic) -> Topic:
    topic.updated_at = datetime.now(timezone.utc)
    with _conn() as c:
        c.execute(
            """INSERT INTO topics
                 (id, title, source, status, notes, last_error, build_target, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, source=excluded.source, status=excluded.status,
                 notes=excluded.notes, last_error=excluded.last_error,
                 build_target=excluded.build_target, updated_at=excluded.updated_at""",
            (
                topic.id, topic.title, topic.source.value, topic.status.value,
                topic.notes, topic.last_error, topic.build_target.value,
                topic.created_at.isoformat(), topic.updated_at.isoformat(),
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
    # last_error column may be absent on very old DBs even after migration runs;
    # tolerate it.
    try:
        last_error = r["last_error"] or ""
    except (IndexError, KeyError):
        last_error = ""
    try:
        build_target = BuildTarget(r["build_target"] or "cloud")
    except (IndexError, KeyError, ValueError):
        build_target = BuildTarget.CLOUD
    return Topic(
        id=r["id"], title=r["title"], source=TopicSource(r["source"]),
        status=TopicStatus(r["status"]), notes=r["notes"], last_error=last_error,
        build_target=build_target,
        created_at=datetime.fromisoformat(r["created_at"]),
        updated_at=datetime.fromisoformat(r["updated_at"]),
    )


# --- scripts ---------------------------------------------------------------
# Scripts are persisted only so a parked (build-on-PC) topic can be handed to
# the local worker later. The cloud writes it; the worker reads it back.

def save_script(script: Script) -> Script:
    lines_json = json.dumps([{"speaker": l.speaker, "text": l.text} for l in script.lines])
    with _conn() as c:
        c.execute(
            """INSERT INTO scripts (id, topic_id, title, summary, lines, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary, lines=excluded.lines""",
            (
                script.id, script.topic_id, script.title, script.summary,
                lines_json, script.created_at.isoformat(),
            ),
        )
    return script


def get_latest_script(topic_id: str) -> Script | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM scripts WHERE topic_id=? ORDER BY created_at DESC LIMIT 1",
            (topic_id,),
        ).fetchone()
    if not r:
        return None
    lines = [ScriptLine(speaker=d["speaker"], text=d["text"]) for d in json.loads(r["lines"])]
    return Script(
        id=r["id"], topic_id=r["topic_id"], title=r["title"], summary=r["summary"],
        lines=lines, created_at=datetime.fromisoformat(r["created_at"]),
    )


# --- episodes --------------------------------------------------------------

def save_episode(ep: Episode) -> Episode:
    tags_csv = ",".join(t.strip() for t in ep.tags if t.strip())
    with _conn() as c:
        c.execute(
            """INSERT INTO episodes
               (id, topic_id, title, summary, audio_path, duration_seconds, rating,
                tags, audio_backend, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, summary=excluded.summary, audio_path=excluded.audio_path,
                 duration_seconds=excluded.duration_seconds, rating=excluded.rating,
                 tags=excluded.tags, audio_backend=excluded.audio_backend""",
            (
                ep.id, ep.topic_id, ep.title, ep.summary, ep.audio_path,
                ep.duration_seconds, ep.rating, tags_csv, ep.audio_backend,
                ep.published_at.isoformat(),
            ),
        )
    return ep


def _parse_tags(csv: str | None) -> list[str]:
    if not csv:
        return []
    return [t.strip() for t in csv.split(",") if t.strip()]


def get_episode(episode_id: str) -> Episode | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
    if not r:
        return None
    return _row_to_episode(r)


def update_episode_meta(episode_id: str, tags: list[str], rating: float | None) -> None:
    """Update only the user-editable metadata for an episode (tags, rating)."""
    tags_csv = ",".join(t.strip() for t in tags if t.strip())
    with _conn() as c:
        c.execute(
            "UPDATE episodes SET tags=?, rating=? WHERE id=?",
            (tags_csv, rating, episode_id),
        )


def delete_episodes_for_topic(topic_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM episodes WHERE topic_id=?", (topic_id,))


def delete_episode(episode_id: str) -> str | None:
    """Remove an episode row. Returns the audio path so callers can unlink the file."""
    with _conn() as c:
        r = c.execute("SELECT audio_path FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
        return r["audio_path"]


def _row_to_episode(r: sqlite3.Row) -> Episode:
    # Tolerate older DBs that don't yet have the tags / audio_backend columns.
    try:
        tags = _parse_tags(r["tags"])
    except (IndexError, KeyError):
        tags = []
    try:
        audio_backend = r["audio_backend"] or ""
    except (IndexError, KeyError):
        audio_backend = ""
    return Episode(
        id=r["id"], topic_id=r["topic_id"], title=r["title"], summary=r["summary"],
        audio_path=r["audio_path"], duration_seconds=r["duration_seconds"],
        rating=r["rating"], tags=tags, audio_backend=audio_backend,
        published_at=datetime.fromisoformat(r["published_at"]),
    )


def get_episodes() -> list[Episode]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM episodes ORDER BY published_at DESC").fetchall()
    return [_row_to_episode(r) for r in rows]
