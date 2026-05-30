"""Top-level pipeline: topic -> research -> script -> audio -> episode -> feed.

Each stage updates the topic status so progress is visible in the UI and a failed
run leaves a clear trail. This is the single entry point a scheduler calls.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .audio import get_audio_generator
from .config import AUDIO_DIR, settings
from .db import save_episode, save_topic
from .feed import build_feed
from .models import Episode, Topic, TopicStatus
from .research import ResearchOrchestrator
from .script import ScriptWriter

log = logging.getLogger("morningcast.pipeline")


def _estimate_duration(words: int) -> int:
    return int(words / 150 * 60)  # ~150 wpm


def produce_episode(topic: Topic) -> Episode:
    """Run the full pipeline for one topic. Persists state at each stage."""
    try:
        topic.status = TopicStatus.RESEARCHING
        save_topic(topic)
        briefing = ResearchOrchestrator().run(topic)
        log.info("Briefing ready for '%s' (%d sources)", topic.title, len(briefing.sources))

        topic.status = TopicStatus.SCRIPTING
        save_topic(topic)
        script = ScriptWriter().write(topic, briefing)
        log.info("Script ready: '%s' (%d words)", script.title, script.word_count())

        topic.status = TopicStatus.GENERATING_AUDIO
        save_topic(topic)
        generator = get_audio_generator()
        out_path = AUDIO_DIR / f"{topic.id}.mp3"
        generator.render(script, out_path)
        log.info("Audio rendered via %s -> %s", generator.name, out_path)

        episode = Episode(
            topic_id=topic.id,
            title=script.title,
            summary=script.summary,
            audio_path=str(out_path),
            duration_seconds=_estimate_duration(script.word_count()),
        )
        save_episode(episode)

        topic.status = TopicStatus.PUBLISHED
        save_topic(topic)

        build_feed()
        log.info("Published '%s' and rebuilt feed", episode.title)
        return episode

    except Exception:
        topic.status = TopicStatus.FAILED
        save_topic(topic)
        log.exception("Pipeline failed for topic '%s'", topic.title)
        raise


def produce_all_queued() -> list[Episode]:
    from .db import get_topics

    episodes = []
    for topic in get_topics(status=TopicStatus.QUEUED):
        episodes.append(produce_episode(topic))
    return episodes
