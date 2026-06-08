"""Local GPU worker for build-on-PC episodes.

Run this on the machine with the GPU. It polls the always-on server for topics
you queued with "Build on my PC", renders them with Dia2, and uploads the
finished mp3 back to the server (which then publishes the episode + rebuilds the
feed). Leave it running; it simply idles while there's nothing to do and picks
up jobs the moment they appear.

    # one-time, on the PC:
    pip install -r requirements.txt
    pip install git+https://github.com/nari-labs/dia.git          # + a CUDA torch

    # then run the worker, pointed at your hosted app:
    MC_BASE_URL=https://<your-app>.up.railway.app \
    MC_WORKER_TOKEN=<same value you set on the server> \
    python -m morningcast.worker

Only depends on the standard library for HTTP, so there's nothing extra to
install beyond the Dia2 audio stack the render itself needs.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .audio import Dia2Audio
from .config import settings
from .models import Script, ScriptLine

log = logging.getLogger("morningcast.worker")

POLL_SECONDS = int(os.getenv("MC_WORKER_POLL_SECONDS", "10"))
# Cap the backoff applied after a network error so a flaky link doesn't park the
# worker for minutes at a time.
MAX_BACKOFF_SECONDS = 120


def _url(path: str) -> str:
    return settings.base_url.rstrip("/") + path


def _post(path: str, *, data: bytes = b"", content_type: str | None = None) -> dict:
    req = urllib.request.Request(_url(path), data=data, method="POST")
    req.add_header("X-Worker-Token", settings.worker_token)
    if content_type:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def claim_job() -> dict | None:
    """Ask the server for the next parked job. Returns the job dict or None."""
    result = _post("/api/worker/claim")
    return result.get("job")


def render_job(job: dict) -> Path:
    """Render a claimed job to a temp mp3 with Dia2 and return its path."""
    script = Script(
        topic_id=job["topic_id"],
        title=job.get("title", ""),
        summary=job.get("summary", ""),
        lines=[ScriptLine(speaker=l["speaker"], text=l["text"]) for l in job["lines"]],
    )
    out_path = Path(tempfile.gettempdir()) / f"coffeecast_{job['topic_id']}.mp3"
    Dia2Audio().render(script, out_path)
    return out_path


def upload_result(topic_id: str, mp3_path: Path) -> None:
    _post(
        f"/api/worker/result/{topic_id}?backend=dia2",
        data=mp3_path.read_bytes(),
        content_type="audio/mpeg",
    )


def report_failure(topic_id: str, error: str) -> None:
    payload = urllib.parse.urlencode({"error": error[:500]}).encode()
    try:
        _post(
            f"/api/worker/fail/{topic_id}",
            data=payload,
            content_type="application/x-www-form-urlencoded",
        )
    except Exception:  # noqa: BLE001 - a failed failure-report shouldn't crash the loop
        log.exception("Could not report failure for %s", topic_id)


def process_one() -> bool:
    """Claim + render + upload a single job. Returns True if work was done."""
    job = claim_job()
    if not job:
        return False
    topic_id = job["topic_id"]
    log.info("Claimed '%s' (%s)", job.get("title", topic_id), topic_id)
    mp3_path: Path | None = None
    try:
        mp3_path = render_job(job)
        upload_result(topic_id, mp3_path)
        log.info("Published '%s' via Dia2", job.get("title", topic_id))
    except Exception as exc:  # noqa: BLE001 - render/upload failures are reported, not fatal
        log.exception("Render failed for %s", topic_id)
        report_failure(topic_id, f"{type(exc).__name__}: {exc}")
    finally:
        if mp3_path is not None:
            mp3_path.unlink(missing_ok=True)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not settings.worker_token:
        raise SystemExit("MC_WORKER_TOKEN is not set — the server would reject this worker.")
    log.info("CoffeeCast worker up. Server=%s, polling every %ss.", settings.base_url, POLL_SECONDS)

    backoff = POLL_SECONDS
    while True:
        try:
            did_work = process_one()
            backoff = POLL_SECONDS  # healthy round: reset backoff
            # Only pause when idle; if we just did work, immediately look for more.
            if not did_work:
                time.sleep(POLL_SECONDS)
        except urllib.error.HTTPError as exc:
            log.error("Server returned %s on claim; retrying in %ss.", exc.code, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except (urllib.error.URLError, OSError) as exc:
            log.warning("Can't reach server (%s); retrying in %ss.", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except KeyboardInterrupt:
            log.info("Worker stopped.")
            return


if __name__ == "__main__":
    main()
