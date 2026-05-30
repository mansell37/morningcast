"""Thin FastAPI app: add topics, view queue, approve/reject suggestions, listen.

Also serves the audio files and the RSS feed so a podcast app can subscribe.
Run with: uvicorn morningcast.web.app:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..config import AUDIO_DIR, FEED_DIR
from ..db import get_episodes, get_topic, get_topics, init_db, save_topic
from ..feed import build_feed
from ..models import Topic, TopicSource, TopicStatus

app = FastAPI(title="MorningCast")


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --- API / actions ---------------------------------------------------------

@app.post("/topics")
def add_topic(title: str = Form(...), notes: str = Form("")):
    save_topic(Topic(title=title, notes=notes, source=TopicSource.USER,
                     status=TopicStatus.QUEUED))
    return RedirectResponse("/", status_code=303)


@app.post("/topics/{topic_id}/approve")
def approve(topic_id: str):
    t = get_topic(topic_id)
    if t:
        t.status = TopicStatus.QUEUED
        save_topic(t)
    return RedirectResponse("/", status_code=303)


@app.post("/topics/{topic_id}/reject")
def reject(topic_id: str):
    t = get_topic(topic_id)
    if t:
        t.status = TopicStatus.REJECTED
        save_topic(t)
    return RedirectResponse("/", status_code=303)


# --- static-ish serving ----------------------------------------------------

@app.get("/audio/{name}")
def audio(name: str):
    path = AUDIO_DIR / name
    if not path.exists():
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/feed.xml")
def feed():
    path = FEED_DIR / "feed.xml"
    if not path.exists():
        build_feed()
    return FileResponse(path, media_type="application/rss+xml")


# --- minimal UI ------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    suggested = get_topics(TopicStatus.SUGGESTED)
    queued = [t for t in get_topics() if t.status in (
        TopicStatus.QUEUED, TopicStatus.RESEARCHING, TopicStatus.SCRIPTING,
        TopicStatus.GENERATING_AUDIO)]
    episodes = get_episodes()

    def topic_row(t: Topic, actions: str = "") -> str:
        note = f"<br><small>{t.notes}</small>" if t.notes else ""
        return f"<li><b>{t.title}</b> <em>({t.status.value})</em>{note} {actions}</li>"

    sug_html = "".join(
        topic_row(
            t,
            f'<form style="display:inline" method="post" action="/topics/{t.id}/approve">'
            f'<button>Approve</button></form> '
            f'<form style="display:inline" method="post" action="/topics/{t.id}/reject">'
            f'<button>Reject</button></form>',
        )
        for t in suggested
    ) or "<li><em>No suggestions yet.</em></li>"

    q_html = "".join(topic_row(t) for t in queued) or "<li><em>Queue empty.</em></li>"

    ep_html = "".join(
        f'<li><b>{e.title}</b> — {e.summary}<br>'
        f'<audio controls src="/audio/{Path(e.audio_path).name}"></audio></li>'
        for e in episodes
    ) or "<li><em>No episodes yet.</em></li>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>MorningCast</title>
<style>body{{font-family:system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
h1{{margin-bottom:0}} ul{{padding-left:1rem}} li{{margin:.6rem 0}}
button{{cursor:pointer}} input,textarea{{width:100%;padding:.4rem;margin:.2rem 0}}</style>
</head><body>
<h1>☕ MorningCast</h1>
<p>Subscribe in your podcast app: <code>/feed.xml</code></p>

<h2>Add a topic</h2>
<form method="post" action="/topics">
  <input name="title" placeholder="Topic title" required>
  <textarea name="notes" placeholder="Optional steer / angle"></textarea>
  <button type="submit">Queue it</button>
</form>

<h2>Curated suggestions</h2><ul>{sug_html}</ul>
<h2>In progress / queued</h2><ul>{q_html}</ul>
<h2>Episodes</h2><ul>{ep_html}</ul>
</body></html>"""
