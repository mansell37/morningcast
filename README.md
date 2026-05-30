# ☕ MorningCast

Concise, AI-curated morning-learning podcasts. Enter a topic (or let it suggest
some), and a two-host episode appears in your podcast app for the commute.

**Pipeline:** topic → Grok research (live search) → Claude synthesis & script →
free TTS → mp3 → private RSS feed.

> 👉 **Read `DECISIONS.md` first** — it explains every choice and how to change it.

---

## Tomorrow's 5-minute setup

### 1. Push to a new GitHub repo
```bash
cd morningcast
git init
git add .
git commit -m "Initial MorningCast build"
# create an empty repo on github.com first, then:
git remote add origin git@github.com:<you>/morningcast.git
git branch -M main
git push -u origin main
```

### 2. Install & configure
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ffmpeg is required at the OS level:
#   Ubuntu: sudo apt install ffmpeg   |   macOS: brew install ffmpeg

cp .env.example .env
# edit .env: add ANTHROPIC_API_KEY and XAI_API_KEY
```

### 3. Verify
```bash
python -m scripts.cli check     # confirms keys + config
python -m pytest                # 5 tests, all offline
```

### 4. First run (with the safe stub audio)
```bash
python -m scripts.cli add "Why container shipping rates swing so wildly"
python -m scripts.cli produce   # research → script → (stub) audio → feed
python -m scripts.cli list
```
Then switch on real audio: `pip install kokoro soundfile`, set
`MC_AUDIO_BACKEND=kokoro` in `.env`, and `produce` again.

---

## Running the web app
```bash
uvicorn morningcast.web.app:app --reload
# open http://localhost:8000  — add topics, approve suggestions, listen
# subscribe your podcast app to  http://localhost:8000/feed.xml
```

## Scheduling (optional)
```bash
python -m scripts.scheduler     # nightly produce @ 04:30, weekly curate Sun 18:00
```
Or use OS cron / Railway cron to call `python -m scripts.cli produce` directly.

---

## Project layout
```
morningcast/
  config.py          settings from .env
  models.py          domain types (Topic, Briefing, Script, Episode)
  research/          Grok + Claude adapters + orchestrator
  script/            briefing → two-host dialogue
  audio/             swappable TTS: stub | kokoro | dia2 | elevenlabs
  feed/              RSS generation
  curation/          weekly topic suggestions
  db/                SQLite persistence (Postgres-ready)
  web/app.py         thin FastAPI UI + audio/feed serving
  pipeline.py        ties it all together
scripts/             cli.py, scheduler.py
tests/               offline pipeline tests
DECISIONS.md         ← review this
```

## Cost note
Grok 4.1 Fast research is cheap (~cents/episode) and xAI offers monthly free
credits worth checking. TTS is free with Kokoro/Dia2. Realistically a few dollars
a month at 2–4 episodes/week even before free credits — or near-zero with them.
```
