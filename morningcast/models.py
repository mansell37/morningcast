"""Core domain models shared across the pipeline."""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class TopicStatus(str, enum.Enum):
    SUGGESTED = "suggested"      # proposed by curation engine, awaiting approval
    QUEUED = "queued"           # approved / user-entered, ready to produce
    RESEARCHING = "researching"
    SCRIPTING = "scripting"
    GENERATING_AUDIO = "generating_audio"
    PUBLISHED = "published"
    FAILED = "failed"
    REJECTED = "rejected"        # user declined a suggestion


class TopicSource(str, enum.Enum):
    USER = "user"
    CURATED = "curated"


@dataclass
class Topic:
    title: str
    source: TopicSource = TopicSource.USER
    status: TopicStatus = TopicStatus.QUEUED
    notes: str = ""                       # optional user steer / curation rationale
    last_error: str = ""                  # set when status == FAILED, surfaced in UI
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class Briefing:
    """Output of the research layer: a tight, fact-checked source doc."""
    topic_id: str
    markdown: str
    sources: list[str] = field(default_factory=list)
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)


@dataclass
class Script:
    """Two-host dialogue. Lines alternate but speaker is explicit per line."""
    topic_id: str
    lines: list["ScriptLine"] = field(default_factory=list)
    title: str = ""
    summary: str = ""
    id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now)

    def word_count(self) -> int:
        return sum(len(l.text.split()) for l in self.lines)


@dataclass
class ScriptLine:
    speaker: str   # "A" or "B"
    text: str


@dataclass
class Episode:
    topic_id: str
    title: str
    summary: str
    audio_path: str            # local path to mp3
    duration_seconds: int = 0
    rating: float | None = None   # for the future shared-library feature
    id: str = field(default_factory=_new_id)
    published_at: datetime = field(default_factory=_now)
