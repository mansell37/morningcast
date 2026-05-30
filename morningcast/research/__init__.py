"""Research layer.

A `Researcher` protocol with concrete adapters for Grok (current-affairs / live
signal) and Claude (synthesis, fact-check, structure). The orchestrator runs Grok
first to gather fresh material, then has Claude turn raw material into a tight,
de-duplicated briefing document.

Pattern mirrors the Protocol/adapter approach used elsewhere so backends are
swappable and individually testable.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import settings
from ..models import Briefing, Topic


@runtime_checkable
class Researcher(Protocol):
    name: str

    def gather(self, topic: Topic) -> str:
        """Return raw research text for the topic."""
        ...


class GrokResearcher:
    """Uses xAI Grok with Live Search for current, X-native signal."""

    name = "grok"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.xai_api_key
        self.model = model or settings.grok_model

    def gather(self, topic: Topic) -> str:
        # Lazy import so the package loads even before deps are installed.
        from openai import OpenAI  # xAI is OpenAI-compatible

        client = OpenAI(api_key=self.api_key, base_url="https://api.x.ai/v1")
        prompt = (
            f"Research the topic: '{topic.title}'.\n"
            f"{('User steer: ' + topic.notes) if topic.notes else ''}\n\n"
            "Gather the most relevant, recent, and noteworthy facts, developments, "
            "and differing viewpoints. Prioritise accuracy and recency. Include "
            "concrete details, figures, and named sources where possible. "
            "Output as structured notes, not prose."
        )
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"search_parameters": {"mode": "auto"}},  # Live Search
        )
        return resp.choices[0].message.content or ""


class ClaudeResearcher:
    """Uses Claude to synthesise, fact-check and structure into a briefing."""

    name = "claude"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.claude_model

    def gather(self, topic: Topic) -> str:
        return self.synthesise(topic, raw_material="")

    def synthesise(self, topic: Topic, raw_material: str) -> Briefing:
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)
        prompt = (
            f"You are preparing a briefing for a short two-host learning podcast on:\n"
            f"'{topic.title}'.\n\n"
            f"Here is raw research material gathered from a live-search assistant:\n"
            f"<material>\n{raw_material}\n</material>\n\n"
            "Produce a tight, accurate briefing in Markdown. Requirements:\n"
            "- Fact-check and drop anything dubious or unsupported.\n"
            "- De-duplicate and organise into a logical arc a listener can follow.\n"
            "- Lead with why this matters, then the substance, then open questions.\n"
            "- Keep it concise: enough for a ~6 minute conversation, not exhaustive.\n"
            "- End with a 'Sources' section listing any sources referenced.\n"
        )
        resp = client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        sources = _extract_sources(text)
        return Briefing(topic_id=topic.id, markdown=text, sources=sources)


def _extract_sources(markdown: str) -> list[str]:
    sources: list[str] = []
    capture = False
    for line in markdown.splitlines():
        if line.strip().lower().startswith("## sources") or line.strip().lower() == "sources":
            capture = True
            continue
        if capture and line.strip():
            sources.append(line.lstrip("-* ").strip())
    return sources


class ResearchOrchestrator:
    """Runs Grok → Claude to produce a Briefing."""

    def __init__(
        self,
        gatherer: Researcher | None = None,
        synthesiser: ClaudeResearcher | None = None,
    ):
        self.gatherer = gatherer or GrokResearcher()
        self.synthesiser = synthesiser or ClaudeResearcher()

    def run(self, topic: Topic) -> Briefing:
        raw = self.gatherer.gather(topic)
        return self.synthesiser.synthesise(topic, raw_material=raw)
