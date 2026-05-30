# DECISIONS.md — review this first ☕

This is the helper file for our morning review. Every meaningful call I made is
listed below with the reasoning and, crucially, **how to change it** if you disagree.
Nothing here is locked in. Veto anything.

---

## The single most important thing to know

**I could not push to GitHub or run anything that needs your API keys** — this
build environment has no access to your accounts, and you (correctly) shouldn't
paste credentials into a chat. So:

- The **entire codebase is built, tested, and working** (5/5 tests pass, the full
  pipeline produces a real mp3 offline).
- **You do two quick things tomorrow**: (1) push to a new GitHub repo, (2) drop
  your API keys into a `.env` file. Exact commands are in `README.md`.

That's the only manual gap, and it's a 5-minute job.

---

## Architecture decisions

### 1. Path B (Claude + free TTS), not NotebookLM
NotebookLM has no public API, so it can't be automated into a "wakes up and the
episode is just there" flow — which is the whole point. We script with Claude and
synthesise audio ourselves. **The audio layer is behind a swappable interface**, so
if a NotebookLM-style API ever appears, it slots in without touching anything else.
*To revisit:* see `morningcast/audio/__init__.py` — add a new class implementing
`AudioGenerator`.

### 2. Two research engines: Grok then Claude
`GrokResearcher` gathers fresh, current-affairs material using xAI **Live Search**;
`ClaudeResearcher` then fact-checks, de-dupes and structures it into a tight
briefing. Same Protocol/adapter pattern you use elsewhere, so each is swappable and
testable in isolation. *To change the division of labour:* `morningcast/research/`.

### 3. Free TTS choice — defaulted to **stub**, recommend **Kokoro**
You said keep it free and swap to ElevenLabs later. I built **four** backends:
- **stub** — silent placeholder, zero accounts. **This is the current default** so
  you can run the whole thing tomorrow before installing any TTS.
- **kokoro** — free, Apache-2.0, **runs on CPU** (good for the A9 Max). My
  recommended real backend. `pip install kokoro soundfile`, then set
  `MC_AUDIO_BACKEND=kokoro`.
- **dia2** — free, Nari Labs, **purpose-built for two-host dialogue** (best free
  "podcast feel"), but prefers a GPU. Try this if the A9 Max has a capable GPU.
- **elevenlabs** — paid, best quality. Wired and ready; uncomment in
  `requirements.txt`, add key, set backend.
*Decision for you:* which real backend to adopt after testing with stub. My vote:
try Dia2 first if your GPU handles it, fall back to Kokoro.

### 4. Models: Grok 4.1 Fast + Claude Opus 4.7
I searched current pricing/availability tonight. **Grok 4.1 Fast** is the cheap
workhorse with Live Search (~$0.20/$0.50 per M tokens) — ideal for research volume.
**Grok 4.3** is the new flagship if you want max quality (~$1.25/$2.50 per M).
Claude model defaults to Opus 4.7. *Both are one-line changes* in `.env`
(`MC_GROK_MODEL`, `MC_CLAUDE_MODEL`).
> ⚠️ xAI offers up to ~$150–175/month in free API credits via their data-sharing
> program (enable in the xAI console). Worth checking — could make research free.

### 5. SQLite now, Postgres-ready for later
State lives in a thin SQLite layer (`morningcast/db/`). The schema is portable; when
you move to Railway and want the shared/rated library, only the connection swaps to
Postgres. No app logic changes.

### 6. RSS feed via stdlib (no dependency)
The feed is simple enough to build with the standard library, keeping the local
footprint tiny. Subscribe in any podcast app at `<base_url>/feed.xml`. *When you
host on Railway*, set `MC_BASE_URL` to the public URL so episode links resolve.

### 7. Curation keeps you in control
The weekly job proposes 1–3 topics (themes from your past picks + current affairs)
but saves them as **SUGGESTED** — they only get produced after you approve in the
web UI. No surprise episodes.

### 8. Local-first, Railway-ready
Runs entirely on the A9 Max now (cron or the included scheduler). The FastAPI app,
job structure and DB are all set up to move to Railway later for scheduled hosting
and the social/sharing features. *Migration is intended to be near-mechanical.*

---

## Episode shape (easy to tune in `.env`)
- ~6 minutes (`MC_TARGET_MINUTES`)
- Two hosts: Alex & Sam (`MC_HOST_A` / `MC_HOST_B`)
- Script prompt explicitly asks for natural disfluencies and cross-talk to narrow
  the gap with NotebookLM's conversational style.

---

## Open questions for you (tomorrow)
1. Real TTS backend: **Dia2 or Kokoro?** (depends on the A9 Max GPU)
2. Grok 4.1 Fast (cheap) or 4.3 (flagship) for research?
3. Host names / voices to your taste?
4. Episode length — is 6 min the right commute dose?
5. Want me to wire ratings + sharing now, or keep that for the Railway phase?

---

## What's NOT built yet (deliberately — Railway phase)
- The social/shared library with ratings (schema has a `rating` field stubbed).
- Auth (not needed while it's just you, local).
- A polished frontend — the current UI is intentionally minimal/functional.

These were always "later" in our plan; flagged here so the scope is explicit.
