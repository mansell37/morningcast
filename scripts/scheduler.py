"""Scheduler. Produces queued episodes nightly and curates suggestions weekly.

Local use: run `python -m scripts.scheduler` and leave it running, or skip this and
use OS cron / Railway cron to call the CLI directly (see DECISIONS.md).
"""
from __future__ import annotations

import logging
import time

import schedule

from morningcast.curation import suggest_topics
from morningcast.db import init_db
from morningcast.pipeline import produce_all_queued

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("morningcast.scheduler")


def nightly() -> None:
    log.info("Nightly production run starting")
    eps = produce_all_queued()
    log.info("Produced %d episode(s)", len(eps))


def weekly() -> None:
    log.info("Weekly curation run starting")
    sugg = suggest_topics()
    log.info("Suggested %d topic(s)", len(sugg))


def main() -> None:
    init_db()
    # Produce overnight so episodes are ready for the commute.
    schedule.every().day.at("04:30").do(nightly)
    # Curate on Sunday evenings.
    schedule.every().sunday.at("18:00").do(weekly)
    log.info("Scheduler running. Ctrl-C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
