"""Central configuration. Reads from environment / .env file.

Nothing secret is hard-coded here. Fill in .env (see .env.example) before running.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:  # dotenv is optional but recommended
    pass


# Project paths -------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MC_DATA_DIR", ROOT / "data"))
AUDIO_DIR = DATA_DIR / "audio"
FEED_DIR = DATA_DIR / "feeds"
DB_PATH = Path(os.getenv("MC_DB_PATH", DATA_DIR / "db" / "morningcast.sqlite3"))

for _p in (DATA_DIR, AUDIO_DIR, FEED_DIR, DB_PATH.parent):
    _p.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    # --- LLM / research providers ---
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    xai_api_key: str = field(default_factory=lambda: os.getenv("XAI_API_KEY", ""))

    # Model strings are easy to change here as providers update them.
    claude_model: str = os.getenv("MC_CLAUDE_MODEL", "claude-opus-4-7")
    grok_model: str = os.getenv("MC_GROK_MODEL", "grok-4.3")  # current flagship; web_search via Agent Tools API

    # --- audio ---
    # Backend options: "dia2", "kokoro", "stub", "elevenlabs"
    audio_backend: str = os.getenv("MC_AUDIO_BACKEND", "kokoro")
    elevenlabs_api_key: str = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY", ""))

    # --- podcast feed metadata ---
    feed_title: str = os.getenv("MC_FEED_TITLE", "MorningCast")
    feed_author: str = os.getenv("MC_FEED_AUTHOR", "MorningCast")
    feed_description: str = os.getenv(
        "MC_FEED_DESCRIPTION", "Concise, AI-curated morning learning podcasts."
    )
    # Public base URL where audio + feed are served (set this when hosting).
    base_url: str = os.getenv("MC_BASE_URL", "http://localhost:8000")

    # --- episode shape ---
    target_minutes: int = int(os.getenv("MC_TARGET_MINUTES", "6"))
    host_a_name: str = os.getenv("MC_HOST_A", "Alex")
    host_b_name: str = os.getenv("MC_HOST_B", "Sam")

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.xai_api_key:
            missing.append("XAI_API_KEY")
        if self.audio_backend == "elevenlabs" and not self.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        return missing


settings = Settings()
