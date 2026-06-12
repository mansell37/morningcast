"""FastAPI app for CoffeeCast.

Coffee-themed, mobile-responsive UI. Server-rendered HTML with a sprinkle of
JavaScript that polls /status and reloads only when something changes (and
never mid-type), so adding topics isn't interrupted by a refresh timer.
Adds topics, runs the pipeline, lets you organise the resulting library.

Run with: uvicorn morningcast.web.app:app --reload
"""
from __future__ import annotations

import html
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ..audio import KOKORO_VOICES
from ..config import AUDIO_DIR, FEED_DIR, settings
from ..db import (
    delete_episode,
    delete_episodes_for_topic,
    delete_topic,
    get_episode,
    get_episodes,
    get_latest_script,
    get_setting,
    get_topic,
    get_topics,
    init_db,
    save_topic,
    set_setting,
    update_episode_meta,
)
from ..feed import build_feed
from ..models import BuildTarget, Episode, Topic, TopicSource, TopicStatus
from ..script import STYLE_PRESETS

app = FastAPI(title="CoffeeCast")


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --- API / actions ---------------------------------------------------------

@app.post("/topics")
def add_topic(title: str = Form(...), notes: str = Form(""), build_target: str = Form("cloud")):
    target = BuildTarget.PC if build_target == "pc" else BuildTarget.CLOUD
    save_topic(Topic(title=title, notes=notes, source=TopicSource.USER,
                     status=TopicStatus.QUEUED, build_target=target))
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


@app.post("/topics/{topic_id}/build-cloud")
def build_cloud(topic_id: str, background: BackgroundTasks):
    """Escape hatch: render a parked (build-on-PC) topic on the cloud instead.

    Reuses the already-written script if we have it (no second research/script
    spend); otherwise falls back to a full re-run.
    """
    from ..pipeline import produce_episode, render_and_publish

    t = get_topic(topic_id)
    if not t or t.status not in (TopicStatus.AWAITING_WORKER, TopicStatus.FAILED):
        return RedirectResponse("/", status_code=303)
    t.build_target = BuildTarget.CLOUD
    save_topic(t)
    script = get_latest_script(t.id)
    if script:
        background.add_task(render_and_publish, t, script, None)
    else:
        background.add_task(produce_episode, t)
    return RedirectResponse("/", status_code=303)


@app.post("/topics/{topic_id}/delete")
def delete_topic_route(topic_id: str):
    """Remove a topic from the queue entirely, along with anything derived from it.

    Deletes the topic plus any script/briefing/episode it produced, and unlinks
    the audio file if one exists. Note: a render already running in the
    background can't be cancelled mid-flight — but the row won't come back.
    """
    audio_paths = delete_topic(topic_id)
    for p in audio_paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass  # file gone or locked; the rows are already removed
    if audio_paths:
        build_feed()  # an episode was removed, so refresh the feed
    return RedirectResponse("/", status_code=303)


@app.post("/episodes/{episode_id}/rerender")
def rerender(episode_id: str, background: BackgroundTasks):
    """Re-queue an episode's topic and re-run the pipeline in the background."""
    from ..pipeline import produce_episode

    ep = get_episode(episode_id)
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


@app.post("/episodes/{episode_id}/delete")
def delete_episode_route(episode_id: str):
    """Delete an episode: removes the DB row, the audio file, and rebuilds the feed.

    Leaves the underlying topic alone so the user can re-queue it later if they
    change their mind.
    """
    audio_path = delete_episode(episode_id)
    if audio_path:
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError:
            pass  # file gone or locked; the row is already removed which is what matters
        build_feed()
    return RedirectResponse("/", status_code=303)


@app.post("/episodes/{episode_id}/edit")
def edit_episode(
    episode_id: str,
    tags: str = Form(""),
    rating: str = Form(""),
):
    """Update tags and rating for an episode (the user-curated metadata)."""
    parsed_rating: float | None = None
    if rating.strip():
        try:
            parsed_rating = float(rating)
            if not (1.0 <= parsed_rating <= 5.0):
                parsed_rating = None
        except ValueError:
            parsed_rating = None
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    update_episode_meta(episode_id, tag_list, parsed_rating)
    return RedirectResponse("/", status_code=303)


@app.post("/settings")
def update_settings(
    style_preset: str = Form(...),
    voice_a: str = Form(...),
    voice_b: str = Form(...),
    target_minutes: str = Form("5"),
):
    if style_preset in STYLE_PRESETS:
        set_setting("style_preset", style_preset)
    valid_voices = {v for v, _ in KOKORO_VOICES}
    if voice_a in valid_voices:
        set_setting("voice_a", voice_a)
    if voice_b in valid_voices:
        set_setting("voice_b", voice_b)
    if target_minutes in {"2", "5", "10"}:
        set_setting("target_minutes", target_minutes)
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


# --- local GPU worker API --------------------------------------------------
# A worker on the user's PC (`python -m morningcast.worker`) claims parked
# build-on-PC jobs, renders them with Dia2 on the GPU, and uploads the mp3.
# All endpoints require the shared MC_WORKER_TOKEN.

def _require_worker(token: str | None) -> None:
    if not settings.worker_token:
        raise HTTPException(status_code=503, detail="Worker endpoints are disabled (set MC_WORKER_TOKEN).")
    if token != settings.worker_token:
        raise HTTPException(status_code=401, detail="Bad or missing worker token.")


@app.post("/api/worker/claim")
def worker_claim(x_worker_token: str | None = Header(default=None)):
    """Hand the oldest parked job to the worker and mark it as rendering.

    Flipping it to GENERATING_AUDIO stops a second claim from grabbing the same
    job. If the PC dies mid-render the topic stays in that state; the user can
    fall back to a cloud build from the UI.
    """
    _require_worker(x_worker_token)
    parked = get_topics(status=TopicStatus.AWAITING_WORKER)
    if not parked:
        return {"job": None}
    topic = parked[-1]  # get_topics is newest-first, so [-1] is the oldest waiting
    script = get_latest_script(topic.id)
    if not script:
        # No script to render — kick it back so it doesn't wedge the queue.
        topic.status = TopicStatus.FAILED
        topic.last_error = "No stored script found for parked topic."
        save_topic(topic)
        return {"job": None}
    topic.status = TopicStatus.GENERATING_AUDIO
    save_topic(topic)
    return {
        "job": {
            "topic_id": topic.id,
            "title": script.title,
            "summary": script.summary,
            "lines": [{"speaker": l.speaker, "text": l.text} for l in script.lines],
        }
    }


@app.post("/api/worker/result/{topic_id}")
async def worker_result(
    topic_id: str,
    request: Request,
    backend: str = "dia2",
    x_worker_token: str | None = Header(default=None),
):
    """Receive a finished mp3 (raw audio/mpeg body) and publish the episode."""
    _require_worker(x_worker_token)
    topic = get_topic(topic_id)
    script = get_latest_script(topic_id)
    if not topic or not script:
        raise HTTPException(status_code=404, detail="Unknown topic or missing script.")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio body.")

    from ..pipeline import render_and_publish

    ep = render_and_publish(topic, script, _PrerenderedAudio(data, backend))
    return {"ok": True, "episode_id": ep.id}


@app.post("/api/worker/fail/{topic_id}")
def worker_fail(
    topic_id: str,
    error: str = Form(""),
    x_worker_token: str | None = Header(default=None),
):
    """Mark a parked job as failed so the UI can surface it / offer a retry."""
    _require_worker(x_worker_token)
    topic = get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Unknown topic.")
    topic.status = TopicStatus.FAILED
    topic.last_error = (error or "Worker reported a render failure.")[:500]
    save_topic(topic)
    return {"ok": True}


class _PrerenderedAudio:
    """Adapter so render_and_publish can 'render' bytes the worker already made."""

    def __init__(self, data: bytes, name: str):
        self._data = data
        self.name = name or "dia2"

    def render(self, script, out_path: Path) -> Path:
        out_path.write_bytes(self._data)
        return out_path


# --- UI helpers ------------------------------------------------------------

def _options(items: list[tuple[str, str]], selected: str) -> str:
    return "".join(
        f'<option value="{v}"{" selected" if v == selected else ""}>{html.escape(label)}</option>'
        for v, label in items
    )


# Stepper visualisation for the topic pipeline.
_PROGRESS = {
    TopicStatus.QUEUED:           ("queued",                "▱▱▱▱"),
    TopicStatus.RESEARCHING:      ("researching",            "▰▱▱▱"),
    TopicStatus.SCRIPTING:        ("scripting",             "▰▰▱▱"),
    TopicStatus.AWAITING_WORKER:  ("waiting for your PC",    "▰▰▱▱"),
    TopicStatus.GENERATING_AUDIO: ("generating audio",       "▰▰▰▱"),
    TopicStatus.PUBLISHED:        ("published",              "▰▰▰▰"),
    TopicStatus.FAILED:           ("failed",                 "✕"),
    TopicStatus.REJECTED:         ("rejected",               ""),
    TopicStatus.SUGGESTED:        ("suggested",              ""),
}
_IN_FLIGHT = {TopicStatus.RESEARCHING, TopicStatus.SCRIPTING, TopicStatus.GENERATING_AUDIO}
# Statuses that keep the page polling: actively-processing *plus* parked-for-PC,
# so the page reloads itself the moment the worker publishes the episode.
_ACTIVE = _IN_FLIGHT | {TopicStatus.AWAITING_WORKER}


def _status_fingerprint(topics: list[Topic], episodes: list[Episode]) -> str:
    """A cheap signature of everything the page cares about.

    Changes when a topic advances (researching -> scripting -> audio), when a
    job finishes/fails, or when a new episode (i.e. new audio) appears. The
    client polls this and only reloads when it changes, so typing isn't
    interrupted every few seconds.
    """
    parts = sorted(f"{t.id}:{t.status.value}" for t in topics)
    return f"{len(episodes)}|" + "|".join(parts)


@app.get("/status")
def status_json():
    topics = get_topics()
    return {
        "in_flight": any(t.status in _ACTIVE for t in topics),
        "fingerprint": _status_fingerprint(topics, get_episodes()),
    }


def _fmt_duration(seconds: int) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _star_display(rating: float | None) -> str:
    if rating is None:
        return '<span class="rating-empty">★★★★★</span>'
    filled = int(round(rating))
    return f'<span class="rating-filled">{"★" * filled}</span><span class="rating-empty">{"★" * (5 - filled)}</span>'


def _filter_and_sort(eps: list[Episode], tag: str, sort: str) -> list[Episode]:
    if tag:
        tag_lower = tag.lower()
        eps = [e for e in eps if any(t.lower() == tag_lower for t in e.tags)]
    if sort == "oldest":
        eps = sorted(eps, key=lambda e: e.published_at)
    elif sort == "rating":
        eps = sorted(eps, key=lambda e: (e.rating or 0), reverse=True)
    elif sort == "longest":
        eps = sorted(eps, key=lambda e: e.duration_seconds, reverse=True)
    elif sort == "shortest":
        eps = sorted(eps, key=lambda e: e.duration_seconds)
    # default newest is already DESC from get_episodes()
    return eps


# --- main page -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(tag: str = "", sort: str = "newest"):
    all_topics = get_topics()
    suggested = [t for t in all_topics if t.status == TopicStatus.SUGGESTED]
    queued = [t for t in all_topics if t.status in (
        TopicStatus.QUEUED, TopicStatus.RESEARCHING, TopicStatus.SCRIPTING,
        TopicStatus.AWAITING_WORKER, TopicStatus.GENERATING_AUDIO, TopicStatus.FAILED)]
    all_episodes = get_episodes()
    episodes = _filter_and_sort(all_episodes, tag, sort)
    any_in_flight = any(t.status in _ACTIVE for t in all_topics)
    all_tags = sorted({t for e in all_episodes for t in e.tags}, key=str.lower)

    # --- settings dropdowns ---
    style_opts = _options(
        [(k, v["label"]) for k, v in STYLE_PRESETS.items()],
        get_setting("style_preset", "dry_british"),
    )
    voice_a_opts = _options(KOKORO_VOICES, get_setting("voice_a", "bm_george"))
    voice_b_opts = _options(KOKORO_VOICES, get_setting("voice_b", "bf_emma"))
    length_opts = _options(
        [("2", "2 min — espresso shot"), ("5", "5 min — flat white"), ("10", "10 min — long black")],
        get_setting("target_minutes", "5"),
    )

    # --- topic rendering ---
    def topic_row(t: Topic, actions: str | None = None) -> str:
        label, bar = _PROGRESS.get(t.status, (t.status.value, ""))
        progress = f'<span class="bar">{bar}</span>' if bar else ""
        status_cls = (
            "ok" if t.status == TopicStatus.PUBLISHED
            else "warn" if t.status in _ACTIVE or t.status == TopicStatus.QUEUED
            else "danger" if t.status == TopicStatus.FAILED
            else "muted"
        )
        # Show where a not-yet-published topic will (or did) render.
        target_chip = ""
        if t.status not in (TopicStatus.PUBLISHED, TopicStatus.REJECTED):
            target_chip = (
                '<span class="chip chip-pc">🖥 your PC · Dia2</span>'
                if t.build_target == BuildTarget.PC
                else '<span class="chip">☁ cloud · Kokoro</span>'
            )
        note_html = f'<div class="note">{html.escape(t.notes)}</div>' if t.notes else ""
        err_html = (
            f'<div class="err"><b>Error:</b> {html.escape(t.last_error)}</div>'
            if t.status == TopicStatus.FAILED and t.last_error else ""
        )
        if actions is None:
            if t.status == TopicStatus.QUEUED:
                actions = (
                    f'<form method="post" action="/topics/{t.id}/produce">'
                    f'<button class="btn-primary">Produce now</button></form>'
                )
            elif t.status == TopicStatus.AWAITING_WORKER:
                # Parked for the PC: offer a cloud fallback if it's not coming online.
                actions = (
                    f'<form method="post" action="/topics/{t.id}/build-cloud">'
                    f'<button class="btn-ghost">Build on cloud instead</button></form>'
                )
            elif t.status == TopicStatus.FAILED:
                actions = (
                    f'<form method="post" action="/topics/{t.id}/produce">'
                    f'<button class="btn-primary">Retry</button></form>'
                )
            else:
                actions = ""
        # Every topic can be removed from the queue outright (deletes the topic
        # and anything derived from it).
        remove_form = (
            f'<form method="post" action="/topics/{t.id}/delete" '
            f'onsubmit="return confirm(\'Remove &quot;{html.escape(t.title)}&quot; '
            f'from the queue? This deletes the topic for good.\');">'
            f'<button class="btn-danger" type="submit">Remove</button></form>'
        )
        return (
            f'<article class="card topic">'
            f'<div class="topic-head">'
            f'<h3>{html.escape(t.title)}</h3>'
            f'<span class="status status-{status_cls}">{html.escape(label)} {progress}</span>'
            f'</div>'
            f'<div class="topic-target">{target_chip}</div>'
            f'{note_html}{err_html}'
            f'<div class="topic-actions">{actions}{remove_form}</div>'
            f'</article>'
        )

    sug_html = "".join(
        topic_row(
            t,
            f'<form method="post" action="/topics/{t.id}/approve">'
            f'<button class="btn-primary">Approve</button></form>'
            f'<form method="post" action="/topics/{t.id}/reject">'
            f'<button class="btn-ghost">Reject</button></form>',
        )
        for t in suggested
    ) or '<p class="empty">No curated suggestions yet.</p>'

    q_html = "".join(topic_row(t) for t in queued) or '<p class="empty">Queue is empty — add a topic above.</p>'

    # --- episode rendering ---
    def episode_card(e: Episode) -> str:
        tags_html = "".join(
            f'<a class="tag" href="/?tag={html.escape(t)}">{html.escape(t)}</a>'
            for t in e.tags
        ) or '<span class="tag tag-empty">untagged</span>'
        backend_chip = (
            f'<span class="chip">via {html.escape(e.audio_backend)}</span>'
            if e.audio_backend else ""
        )
        duration_chip = (
            f'<span class="chip">{_fmt_duration(e.duration_seconds)}</span>'
            if e.duration_seconds else ""
        )
        audio_name = Path(e.audio_path).name
        rating_val = "" if e.rating is None else f"{e.rating:.0f}"
        return f"""
        <article class="card episode">
          <div class="ep-head">
            <h3>{html.escape(e.title)}</h3>
            <div class="ep-meta">{duration_chip}{backend_chip}</div>
          </div>
          <p class="summary">{html.escape(e.summary)}</p>
          <audio controls preload="none" src="/audio/{html.escape(audio_name)}"></audio>
          <div class="ep-tags">{tags_html}</div>
          <div class="ep-rating">{_star_display(e.rating)}</div>
          <div class="ep-actions">
            <form method="post" action="/episodes/{e.id}/rerender">
              <button class="btn-ghost" type="submit">Re-render</button>
            </form>
            <form method="post" action="/episodes/{e.id}/delete"
                  onsubmit="return confirm('Delete &quot;{html.escape(e.title)}&quot;? The mp3 will be removed too. The original topic stays so you can re-queue it later.');">
              <button class="btn-danger" type="submit">Delete podcast</button>
            </form>
          </div>
          <details class="ep-edit">
            <summary>Edit tags &amp; rating</summary>
            <form method="post" action="/episodes/{e.id}/edit" class="edit-form">
              <label>Tags (comma-separated)
                <input name="tags" value="{html.escape(",".join(e.tags))}" placeholder="finance, tech, deep-dive">
              </label>
              <label>Rating
                <select name="rating">
                  <option value=""{"" if e.rating is not None else " selected"}>— unrated —</option>
                  <option value="1"{" selected" if rating_val == "1" else ""}>★ — meh</option>
                  <option value="2"{" selected" if rating_val == "2" else ""}>★★ — okay</option>
                  <option value="3"{" selected" if rating_val == "3" else ""}>★★★ — good</option>
                  <option value="4"{" selected" if rating_val == "4" else ""}>★★★★ — great</option>
                  <option value="5"{" selected" if rating_val == "5" else ""}>★★★★★ — keeper</option>
                </select>
              </label>
              <div class="form-actions">
                <button class="btn-primary" type="submit">Save</button>
              </div>
            </form>
          </details>
        </article>
        """

    ep_html = "".join(episode_card(e) for e in episodes) or (
        '<p class="empty">No episodes match those filters yet.</p>' if (tag or sort != "newest")
        else '<p class="empty">No episodes yet — queue a topic and click <b>Produce now</b>.</p>'
    )

    # --- filter bar ---
    tag_opts = _options(
        [("", "all tags")] + [(t, t) for t in all_tags],
        tag,
    )
    sort_opts = _options(
        [
            ("newest", "newest first"),
            ("oldest", "oldest first"),
            ("rating", "top rated"),
            ("longest", "longest"),
            ("shortest", "shortest"),
        ],
        sort,
    )

    # --- banners ---
    # No more <meta refresh> hammering the page every 5s (it nuked half-typed
    # topics). Instead a tiny poller checks /status and only reloads when
    # something actually changes — and never while you're mid-type. Plus a
    # manual "Refresh now" button for the impatient.
    fingerprint = _status_fingerprint(all_topics, all_episodes)
    refresh = (
        f'<script>window.__cc_fingerprint={fingerprint!r};'
        f'window.__cc_in_flight={"true" if any_in_flight else "false"};</script>'
        if any_in_flight else ''
    )
    in_flight_banner = (
        '<div class="banner banner-warn"><b>Episode in progress.</b> '
        'This page refreshes itself when new audio is ready — type away. '
        '<button type="button" class="btn-ghost" onclick="location.reload()">'
        'Refresh now</button></div>'
        if any_in_flight else ''
    )

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>CoffeeCast</title>
<style>{_CSS}</style>
</head><body>
<header class="masthead">
  <div class="masthead-inner">
    <h1>☕ CoffeeCast</h1>
    <p class="tagline">Concise, AI-curated morning podcasts.</p>
  </div>
</header>

<main class="container">
  {in_flight_banner}

  <section class="card section">
    <h2>Add a topic</h2>
    <form method="post" action="/topics" class="add-form">
      <input name="title" placeholder="What should we cover next?" required>
      <textarea name="notes" placeholder="Optional steer or angle (e.g. focus on Sydney market)"></textarea>
      <fieldset class="build-target">
        <legend>Build with</legend>
        <label><input type="radio" name="build_target" value="cloud" checked>
          ☁ Cloud now <span class="hint">— Kokoro, builds straight away</span></label>
        <label><input type="radio" name="build_target" value="pc">
          🖥 My PC <span class="hint">— Dia2, waits for the PC to come online</span></label>
      </fieldset>
      <button class="btn-primary" type="submit">Queue it</button>
    </form>
  </section>

  <section class="card section">
    <h2>Brew settings</h2>
    <form method="post" action="/settings" class="settings-form">
      <label>Style
        <select name="style_preset">{style_opts}</select>
      </label>
      <label>Length
        <select name="target_minutes">{length_opts}</select>
      </label>
      <label>Host A &mdash; {html.escape(settings.host_a_name)}
        <select name="voice_a">{voice_a_opts}</select>
      </label>
      <label>Host B &mdash; {html.escape(settings.host_b_name)}
        <select name="voice_b">{voice_b_opts}</select>
      </label>
      <div class="form-actions">
        <button class="btn-primary" type="submit">Save settings</button>
        <small class="muted">Applies to the next episode you produce.</small>
      </div>
    </form>
  </section>

  {('<section class="section"><h2>Curated suggestions</h2>' + sug_html + '</section>') if suggested else ''}

  <section class="section">
    <h2>In progress &amp; queued</h2>
    {q_html}
  </section>

  <section class="section">
    <h2>Library</h2>
    <form method="get" action="/" class="filter-bar">
      <label>Tag
        <select name="tag" onchange="this.form.submit()">{tag_opts}</select>
      </label>
      <label>Sort
        <select name="sort" onchange="this.form.submit()">{sort_opts}</select>
      </label>
      <noscript><button class="btn-ghost" type="submit">Apply</button></noscript>
      {('<a class="btn-ghost" href="/">Clear</a>' if (tag or sort != "newest") else '')}
    </form>
    <div class="episodes">{ep_html}</div>
  </section>

  <footer class="footer">
    <p>Subscribe in your podcast app: <a href="/feed.xml"><code>/feed.xml</code></a></p>
  </footer>
</main>
<script>
(function () {{
  if (!window.__cc_in_flight) return;          // nothing running, nothing to poll
  var baseline = window.__cc_fingerprint;
  function busyTyping() {{
    var el = document.activeElement;
    return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")
           && (el.value || "").trim() !== "";
  }}
  async function poll() {{
    try {{
      var r = await fetch("/status", {{cache: "no-store"}});
      var s = await r.json();
      if (s.fingerprint !== baseline && !busyTyping()) {{
        location.reload();                       // new audio / status change -> refresh
      }}
    }} catch (e) {{ /* transient network blip, try again next tick */ }}
  }}
  setInterval(poll, 5000);
}})();
</script>
</body></html>"""


_CSS = r"""
:root {
  --bg:        #fbf6ec;
  --bg-card:   #ffffff;
  --bg-accent: #f3e7d0;
  --text:      #2d1810;
  --text-mute: #7a5c44;
  --accent:    #8b4513;
  --accent-hi: #6b3410;
  --highlight: #d4a574;
  --ok:        #4a7c2a;
  --warn:      #c47a1a;
  --danger:    #b23a48;
  --border:    rgba(45, 24, 16, 0.12);
  --shadow:    0 1px 3px rgba(45, 24, 16, 0.08), 0 6px 16px rgba(45, 24, 16, 0.04);
  --radius:    10px;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hi); text-decoration: underline; }

/* masthead */
.masthead {
  background: linear-gradient(135deg, #6b3410 0%, #8b4513 55%, #b86f2f 100%);
  color: #fff8ec;
  padding: 1.4rem 1rem 1.6rem;
  box-shadow: 0 2px 8px rgba(45, 24, 16, 0.15);
}
.masthead-inner { max-width: 900px; margin: 0 auto; }
.masthead h1 { margin: 0; font-size: 1.85rem; letter-spacing: -0.5px; }
.tagline { margin: 0.25rem 0 0; opacity: 0.85; font-size: 1rem; }

/* layout */
.container { max-width: 900px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
.section { margin-top: 2rem; }
.section h2 {
  font-size: 1.15rem;
  margin: 0 0 0.8rem;
  letter-spacing: 0.2px;
  color: var(--accent-hi);
}
.empty {
  color: var(--text-mute);
  font-style: italic;
  padding: 0.6rem 0;
}
.muted { color: var(--text-mute); }

/* cards */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 1.1rem 1.2rem;
  margin-bottom: 1rem;
}

/* banners */
.banner {
  padding: 0.7rem 1rem;
  border-radius: var(--radius);
  border-left: 4px solid var(--warn);
  background: #fff5e0;
  margin-bottom: 1rem;
}
.banner-warn { border-left-color: var(--warn); }

/* forms */
form label {
  display: block;
  margin: 0.55rem 0;
  font-size: 0.93rem;
  color: var(--text-mute);
}
input[type=text], input:not([type]), textarea, select {
  width: 100%;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  font: inherit;
  color: var(--text);
  margin-top: 0.2rem;
}
textarea { min-height: 70px; resize: vertical; }
input:focus, textarea:focus, select:focus {
  outline: 2px solid var(--highlight);
  outline-offset: 1px;
}

/* buttons */
button, .btn-primary, .btn-ghost {
  font: inherit;
  cursor: pointer;
  border-radius: 6px;
  padding: 0.55rem 1.1rem;
  border: 1px solid transparent;
  transition: background 120ms ease, transform 80ms ease;
  min-height: 44px; /* mobile touch target */
}
.btn-primary, button[type=submit] {
  background: var(--accent);
  color: #fff8ec;
  border-color: var(--accent);
}
.btn-primary:hover, button[type=submit]:hover { background: var(--accent-hi); }
.btn-ghost {
  background: transparent;
  color: var(--accent);
  border-color: var(--border);
  text-decoration: none;
  display: inline-block;
}
.btn-ghost:hover { background: var(--bg-accent); color: var(--accent-hi); text-decoration: none; }
.btn-danger {
  background: transparent;
  color: var(--danger);
  border-color: var(--border);
}
.btn-danger:hover { background: var(--danger); color: #fff; border-color: var(--danger); }

.form-actions {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 0.5rem;
}

/* settings form: two-column on wide screens */
.settings-form { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem 1rem; }
.settings-form .form-actions { grid-column: 1 / -1; }
@media (max-width: 600px) {
  .settings-form { grid-template-columns: 1fr; }
}

/* topic cards */
.topic .topic-head {
  display: flex; gap: 0.75rem; align-items: baseline;
  justify-content: space-between; flex-wrap: wrap;
}
.topic h3 { margin: 0; font-size: 1.05rem; }
.topic .note { margin-top: 0.35rem; color: var(--text-mute); font-size: 0.92rem; }
.topic .err  { margin-top: 0.4rem; color: var(--danger); font-size: 0.88rem; }
.topic-actions { margin-top: 0.7rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
.topic-actions form { margin: 0; }

.status {
  font-size: 0.8rem;
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  white-space: nowrap;
  font-weight: 600;
}
.status-ok     { background: #e7f3df; color: var(--ok); }
.status-warn   { background: #fbeacf; color: var(--warn); }
.status-danger { background: #f6dadf; color: var(--danger); }
.status-muted  { background: var(--bg-accent); color: var(--text-mute); }
.bar {
  font-family: ui-monospace, "SF Mono", Consolas, monospace;
  letter-spacing: 1px;
  margin-left: 0.4rem;
}

/* episode cards */
.episodes { display: flex; flex-direction: column; gap: 0.6rem; }
.episode .ep-head {
  display: flex; gap: 0.75rem; align-items: baseline;
  justify-content: space-between; flex-wrap: wrap;
}
.episode h3 { margin: 0; font-size: 1.1rem; line-height: 1.3; }
.episode .summary { margin: 0.35rem 0 0.75rem; color: var(--text-mute); font-size: 0.95rem; }
.episode audio { width: 100%; margin: 0.4rem 0 0.6rem; }
.ep-meta { display: flex; gap: 0.4rem; flex-wrap: wrap; }
.chip {
  background: var(--bg-accent);
  color: var(--accent-hi);
  font-size: 0.72rem;
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  font-weight: 600;
  letter-spacing: 0.3px;
}
.chip-pc { background: #2e2a4d; color: #cfc6ff; }
.topic-target { margin-top: 0.4rem; }

.build-target {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.5rem 0.75rem;
  margin: 0.25rem 0 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.build-target legend { font-size: 0.8rem; color: var(--text-mute); padding: 0 0.35rem; }
.build-target label { display: flex; align-items: baseline; gap: 0.45rem; cursor: pointer; }
.build-target .hint { color: var(--text-mute); font-size: 0.82rem; }

.ep-tags {
  display: flex; gap: 0.35rem; flex-wrap: wrap; margin-bottom: 0.4rem;
}
.tag {
  background: var(--bg-accent);
  color: var(--accent-hi);
  padding: 0.18rem 0.6rem;
  border-radius: 6px;
  font-size: 0.82rem;
  border: 1px solid transparent;
}
.tag:hover { background: var(--highlight); color: var(--text); text-decoration: none; }
.tag-empty { background: transparent; color: var(--text-mute); border-color: var(--border); font-style: italic; }

.ep-rating { font-size: 1rem; margin-bottom: 0.5rem; letter-spacing: 1px; }
.rating-filled { color: var(--highlight); }
.rating-empty  { color: rgba(45, 24, 16, 0.18); }

.ep-edit {
  border-top: 1px dashed var(--border);
  padding-top: 0.7rem;
  margin-top: 0.4rem;
}
.ep-edit summary {
  cursor: pointer;
  color: var(--text-mute);
  font-size: 0.88rem;
  list-style: none;
}
.ep-edit summary::before { content: "▸ "; }
.ep-edit[open] summary::before { content: "▾ "; }
.edit-form { margin-top: 0.6rem; }
.ep-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin: 0.2rem 0 0.6rem;
}
.ep-actions form { margin: 0; }

/* filter bar */
.filter-bar {
  display: flex; gap: 0.8rem; align-items: end; flex-wrap: wrap;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.7rem 1rem;
  margin-bottom: 0.8rem;
  box-shadow: var(--shadow);
}
.filter-bar label { margin: 0; flex: 1 1 160px; }
.filter-bar .btn-ghost { align-self: end; }

/* footer */
.footer {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px dashed var(--border);
  color: var(--text-mute);
  text-align: center;
  font-size: 0.88rem;
}

/* mobile tightening */
@media (max-width: 600px) {
  .masthead { padding: 1.1rem 0.9rem 1.3rem; }
  .masthead h1 { font-size: 1.5rem; }
  .container { padding: 1rem 0.7rem 3rem; }
  .card { padding: 0.9rem 0.95rem; }
  .episode h3 { font-size: 1rem; }
}
"""
