"""Top-level pipeline: topic -> research -> script -> audio -> episode -> feed.

Each stage updates the topic status so progress is visible in the UI and a failed
run leaves a clear trail. This is the single entry point a scheduler calls.
"""
from __future__ import annotations

import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .audio import AudioGenerator, get_audio_generator
from .config import AUDIO_DIR, DATA_DIR, settings
from .db import save_episode, save_script, save_topic
from .feed import build_feed
from .models import BuildTarget, Episode, Script, Topic, TopicStatus
from .research import ResearchOrchestrator
from .script import ScriptWriter

log = logging.getLogger("morningcast.pipeline")


def _ensure_file_handler() -> None:
    """Attach a rotating file handler to the morningcast logger once.

    Failures inside background tasks otherwise vanish into uvicorn's stderr,
    which can be invisible depending on how the server was launched. The log
    file gives us a permanent record we can grep.
    """
    root = logging.getLogger("morningcast")
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    log_path = DATA_DIR / "morningcast.log"
    handler = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root.addHandler(handler)


_ensure_file_handler()


def _estimate_duration(words: int) -> int:
    return int(words / 150 * 60)  # ~150 wpm


def _research_and_script(topic: Topic) -> Script:
    """Stages 1-2: research then script. Persists the script and topic state."""
    topic.status = TopicStatus.RESEARCHING
    save_topic(topic)
    briefing = ResearchOrchestrator().run(topic)
    log.info("Briefing ready for '%s' (%d sources)", topic.title, len(briefing.sources))

    topic.status = TopicStatus.SCRIPTING
    save_topic(topic)
    script = ScriptWriter().write(topic, briefing)
    save_script(script)  # persisted so a parked PC job can be handed to the worker
    log.info("Script ready: '%s' (%d words)", script.title, script.word_count())
    return script


def render_and_publish(
    topic: Topic, script: Script, generator: AudioGenerator | None = None
) -> Episode:
    """Stage 3+: render audio for an existing script, save the episode, rebuild feed.

    Used by the cloud path directly and by the worker upload path (which passes a
    pre-rendered mp3 via a generator that just copies bytes into place).
    """
    topic.status = TopicStatus.GENERATING_AUDIO
    save_topic(topic)
    generator = generator or get_audio_generator()
    out_path = AUDIO_DIR / f"{topic.id}.mp3"
    generator.render(script, out_path)
    log.info("Audio rendered via %s -> %s", generator.name, out_path)

    episode = Episode(
        topic_id=topic.id,
        title=script.title,
        summary=script.summary,
        audio_path=str(out_path),
        duration_seconds=_estimate_duration(script.word_count()),
        audio_backend=generator.name,
    )
    save_episode(episode)

    topic.status = TopicStatus.PUBLISHED
    save_topic(topic)

    build_feed()
    log.info("Published '%s' and rebuilt feed", episode.title)
    return episode


def produce_episode(topic: Topic) -> Episode | None:
    """Run the pipeline for one topic. Persists state at each stage.

    For a cloud build this renders audio inline and returns the Episode. For a
    build-on-PC topic it stops after scripting, parks the topic as
    AWAITING_WORKER, and returns None — the local GPU worker finishes it later.
    """
    try:
        script = _research_and_script(topic)

        if topic.build_target == BuildTarget.PC:
            topic.status = TopicStatus.AWAITING_WORKER
            save_topic(topic)
            log.info("Parked '%s' for the local Dia2 worker", topic.title)
            return None

        return render_and_publish(topic, script)

    except Exception as exc:
        topic.status = TopicStatus.FAILED
        # Compact one-line summary for the UI; full traceback goes to the log.
        topic.last_error = f"{type(exc).__name__}: {exc}"[:500]
        save_topic(topic)
        log.error(
            "Pipeline failed for topic '%s' (%s)\n%s",
            topic.title, topic.last_error, traceback.format_exc(),
        )
        raise


def produce_all_queued() -> list[Episode]:
    from .db import get_topics

    episodes = []
    for topic in get_topics(status=TopicStatus.QUEUED):
        episodes.append(produce_episode(topic))
    return episodes
