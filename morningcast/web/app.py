"""Thin FastAPI app: add topics, view queue, approve/reject suggestions, listen.

Also serves the audio files and the RSS feed so a podcast app can subscribe.
Run with: uvicorn morningcast.web.app:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..audio import KOKORO_VOICES
from ..config import AUDIO_DIR, FEED_DIR, settings
from ..db import (
    delete_episodes_for_topic,
    get_episodes,
    get_setting,
    get_topic,
    get_topics,
    init_db,
    save_topic,
    set_setting,
)
from ..feed import build_feed
from ..models import Topic, TopicSource, TopicStatus
from ..script import STYLE_PRESETS

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


@app.post("/topics/{topic_id}/produce")
def produce(topic_id: str, background: BackgroundTasks):
    """Run the pipeline for a single topic in the background."""
    from ..pipeline import produce_episode

    t = get_topic(topic_id)
    if not t or t.status not in (TopicStatus.QUEUED, TopicStatus.FAILED, TopicStatus.SUGGESTED):
        return RedirectResponse("/", status_code=303)
    t.status = TopicStatus.QUEUED
    save_topic(t)
    background.add_task(produce_episode, t)
    return RedirectResponse("/", status_code=303)


@app.post("/episodes/{episode_id}/rerender")
def rerender(episode_id: str, background: BackgroundTasks):
    """Re-queue an episode's topic and re-run the pipeline in the background.

    The page redirects immediately; the user can refresh to watch the status
    march through researching → scripting → generating_audio → published.
    """
    from ..pipeline import produce_episode

    ep = next((e for e in get_episodes() if e.id == episode_id), None)
    if not ep:
        return RedirectResponse("/", status_code=303)
    t = get_topic(ep.topic_id)
    if not t:
        return RedirectResponse("/", status_code=303)
    delete_episodes_for_topic(t.id)
    t.status = TopicStatus.QUEUED
    save_topic(t)
    background.add_task(produce_episode, t)
    return RedirectResponse("/", status_code=303)


@app.post("/settings")
def update_settings(
    style_preset: str = Form(...),
    voice_a: str = Form(...),
    voice_b: str = Form(...),
):
    if style_preset in STYLE_PRESETS:
        set_setting("style_preset", style_preset)
    valid_voices = {v for v, _ in KOKORO_VOICES}
    if voice_a in valid_voices:
        set_setting("voice_a", voice_a)
    if voice_b in valid_voices:
        set_setting("voice_b", voice_b)
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

def _options(items: list[tuple[str, str]], selected: str) -> str:
    return "".join(
        f'<option value="{v}"{" selected" if v == selected else ""}>{label}</option>'
        for v, label in items
    )


# Stepper visualisation for the topic pipeline. Filled = done/in-progress,
# empty = not yet. The "current" stage is the rightmost filled segment.
_PROGRESS = {
    TopicStatus.QUEUED:           ("queued",                "▱▱▱▱"),
    TopicStatus.RESEARCHING:      ("researching",            "▰▱▱▱"),
    TopicStatus.SCRIPTING:        ("scripting",             "▰▰▱▱"),
    TopicStatus.GENERATING_AUDIO: ("generating audio",       "▰▰▰▱"),
    TopicStatus.PUBLISHED:        ("published",              "▰▰▰▰"),
    TopicStatus.FAILED:           ("failed",                 "✕"),
    TopicStatus.REJECTED:         ("rejected",               ""),
    TopicStatus.SUGGESTED:        ("suggested",              ""),
}
_IN_FLIGHT = {TopicStatus.RESEARCHING, TopicStatus.SCRIPTING, TopicStatus.GENERATING_AUDIO}


@app.get("/", response_class=HTMLResponse)
def home():
    all_topics = get_topics()
    suggested = [t for t in all_topics if t.status == TopicStatus.SUGGESTED]
    queued = [t for t in all_topics if t.status in (
        TopicStatus.QUEUED, TopicStatus.RESEARCHING, TopicStatus.SCRIPTING,
        TopicStatus.GENERATING_AUDIO, TopicStatus.FAILED)]
    episodes = get_episodes()
    any_in_flight = any(t.status in _IN_FLIGHT for t in all_topics)

    style_opts = _options(
        [(k, v["label"]) for k, v in STYLE_PRESETS.items()],
        get_setting("style_preset", "dry_british"),
    )
    voice_a_opts = _options(KOKORO_VOICES, get_setting("voice_a", "bm_george"))
    voice_b_opts = _options(KOKORO_VOICES, get_setting("voice_b", "bf_emma"))

    def topic_row(t: Topic, actions: str | None = None) -> str:
        label, bar = _PROGRESS.get(t.status, (t.status.value, ""))
        progress = f' <span class="bar">{bar}</span>' if bar else ""
        note = f"<br><small>{t.notes}</small>" if t.notes else ""
        err = (
            f'<br><small style="color:#a00"><b>Error:</b> {t.last_error}</small>'
            if t.status == TopicStatus.FAILED and t.last_error else ""
        )
        note = note + err
        if actions is None:
            if t.status == TopicStatus.QUEUED:
                actions = (
                    f'<form style="display:inline" method="post" action="/topics/{t.id}/produce">'
                    f'<button>Produce now</button></form>'
                )
            elif t.status == TopicStatus.FAILED:
                actions = (
                    f'<form style="display:inline" method="post" action="/topics/{t.id}/produce">'
                    f'<button>Retry</button></form>'
                )
            else:
                actions = ""
        return f'<li><b>{t.title}</b> <em>({label}{progress})</em>{note} {actions}</li>'

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
        f'<audio controls src="/audio/{Path(e.audio_path).name}"></audio>'
        f'<form style="display:inline;margin-left:.5rem" method="post" '
        f'action="/episodes/{e.id}/rerender">'
        f'<button title="Re-run the pipeline with current voice/style settings">'
        f'Re-render</button></form></li>'
        for e in episodes
    ) or "<li><em>No episodes yet.</em></li>"

    refresh = '<meta http-equiv="refresh" content="5">' if any_in_flight else ''
    in_flight_banner = (
        '<p style="background:#fff8d8;padding:.5rem .8rem;border-left:3px solid #d4a017;'
        'border-radius:3px"><b>Episode in progress.</b> This page auto-refreshes every 5s '
        'while work is running.</p>'
        if any_in_flight else ''
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
{refresh}
<title>MorningCast</title>
<style>body{{font-family:system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
h1{{margin-bottom:0}} ul{{padding-left:1rem}} li{{margin:.6rem 0}}
button{{cursor:pointer}} input,textarea{{width:100%;padding:.4rem;margin:.2rem 0}}
.bar{{font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:1px;color:#0a7}}</style>
</head><body>
<h1>☕ MorningCast</h1>
<p>Subscribe in your podcast app: <code>/feed.xml</code></p>
{in_flight_banner}

<h2>Add a topic</h2>
<form method="post" action="/topics">
  <input name="title" placeholder="Topic title" required>
  <textarea name="notes" placeholder="Optional steer / angle"></textarea>
  <button type="submit">Queue it</button>
</form>

<h2>Voice &amp; style</h2>
<form method="post" action="/settings">
  <label>Style: <select name="style_preset">{style_opts}</select></label>
  <label>Host A ({settings.host_a_name}): <select name="voice_a">{voice_a_opts}</select></label>
  <label>Host B ({settings.host_b_name}): <select name="voice_b">{voice_b_opts}</select></label>
  <button type="submit">Save settings</button>
  <small>Applies to the next episode you <em>produce</em>. Re-queue existing topics to re-render with new voices.</small>
</form>

<h2>Curated suggestions</h2><ul>{sug_html}</ul>
<h2>In progress / queued</h2><ul>{q_html}</ul>
<h2>Episodes</h2><ul>{ep_html}</ul>
</body></html>"""
