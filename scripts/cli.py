"""Command-line interface for running MorningCast without the web app.

Examples:
    python -m scripts.cli add "The economics of port congestion"
    python -m scripts.cli produce          # produce everything QUEUED
    python -m scripts.cli curate           # generate suggestions
    python -m scripts.cli feed             # rebuild RSS feed
    python -m scripts.cli list             # show topics + episodes
    python -m scripts.cli check            # verify config / keys
"""
from __future__ import annotations

import sys

from morningcast.config import settings
from morningcast.curation import suggest_topics
from morningcast.db import get_episodes, get_topics, init_db, save_topic
from morningcast.feed import build_feed
from morningcast.models import Topic, TopicSource, TopicStatus
from morningcast.pipeline import produce_all_queued


def cmd_add(args: list[str]) -> None:
    title = " ".join(args)
    if not title:
        print("Usage: cli add <topic title>")
        return
    t = save_topic(Topic(title=title, source=TopicSource.USER, status=TopicStatus.QUEUED))
    print(f"Queued: {t.title} ({t.id})")


def cmd_produce(_: list[str]) -> None:
    eps = produce_all_queued()
    print(f"Produced {len(eps)} episode(s).")
    for e in eps:
        print(f"  - {e.title} -> {e.audio_path}")


def cmd_curate(_: list[str]) -> None:
    sugg = suggest_topics()
    print(f"Suggested {len(sugg)} topic(s):")
    for s in sugg:
        print(f"  - {s.title}  ({s.notes})")


def cmd_feed(_: list[str]) -> None:
    print(f"Feed written to {build_feed()}")


def cmd_list(_: list[str]) -> None:
    print("== Topics ==")
    for t in get_topics():
        print(f"  [{t.status.value:>16}] {t.title}")
    print("== Episodes ==")
    for e in get_episodes():
        print(f"  {e.title} ({e.duration_seconds}s)")


def cmd_check(_: list[str]) -> None:
    init_db()
    missing = settings.missing_keys()
    print(f"Audio backend : {settings.audio_backend}")
    print(f"Claude model  : {settings.claude_model}")
    print(f"Grok model    : {settings.grok_model}")
    if missing:
        print(f"MISSING KEYS  : {', '.join(missing)} (add to .env)")
    else:
        print("All required keys present.")


COMMANDS = {
    "add": cmd_add, "produce": cmd_produce, "curate": cmd_curate,
    "feed": cmd_feed, "list": cmd_list, "check": cmd_check,
}


def main() -> None:
    init_db()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
