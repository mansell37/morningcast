"""Tests that exercise the pipeline without any API keys.

LLM/research stages are monkeypatched so the whole flow (script parsing -> stub
audio -> episode -> feed -> db) runs offline. Run with: pytest
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("MC_DATA_DIR", tmp)
    monkeypatch.setenv("MC_DB_PATH", str(Path(tmp) / "test.sqlite3"))
    monkeypatch.setenv("MC_AUDIO_BACKEND", "stub")
    # Reimport config fresh so paths pick up the temp dir.
    import importlib
    import morningcast.config as cfg
    importlib.reload(cfg)
    yield


def _have_ffmpeg() -> bool:
    from shutil import which
    return which("ffmpeg") is not None


def test_models_roundtrip():
    from morningcast.models import Topic, TopicStatus
    t = Topic(title="Test")
    assert t.status == TopicStatus.QUEUED
    assert len(t.id) == 12


def test_db_topic_crud():
    import importlib
    import morningcast.db as db
    importlib.reload(db)
    from morningcast.models import Topic
    db.init_db()
    t = db.save_topic(Topic(title="DB topic"))
    fetched = db.get_topic(t.id)
    assert fetched is not None
    assert fetched.title == "DB topic"


def test_script_json_parsing():
    from morningcast.script import _safe_json
    raw = '```json\n{"title":"X","summary":"y","lines":[{"speaker":"A","text":"hi"}]}\n```'
    data = _safe_json(raw)
    assert data["title"] == "X"
    assert data["lines"][0]["speaker"] == "A"


def test_stub_audio_and_word_count():
    from morningcast.models import Script, ScriptLine
    s = Script(topic_id="t", lines=[ScriptLine("A", "hello there"), ScriptLine("B", "hi")])
    assert s.word_count() == 3


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_full_pipeline_offline(monkeypatch):
    import importlib
    import morningcast.config as cfg
    importlib.reload(cfg)
    import morningcast.db as db
    importlib.reload(db)
    import morningcast.pipeline as pipe
    importlib.reload(pipe)

    from morningcast.models import Briefing, Script, ScriptLine, Topic

    # Patch research + script stages so no network is needed.
    monkeypatch.setattr(
        pipe.ResearchOrchestrator, "run",
        lambda self, topic: Briefing(topic_id=topic.id, markdown="# brief", sources=["s1"]),
    )
    monkeypatch.setattr(
        pipe.ScriptWriter, "write",
        lambda self, topic, briefing: Script(
            topic_id=topic.id, title="Ep Title", summary="summary",
            lines=[ScriptLine("A", "Hello and welcome"), ScriptLine("B", "Great to be here")],
        ),
    )

    db.init_db()
    ep = pipe.produce_episode(Topic(title="Pipeline test"))
    assert ep.title == "Ep Title"
    assert Path(ep.audio_path).exists()
