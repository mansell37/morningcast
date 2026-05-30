"""Script generation.

Turns a Briefing into a natural two-host dialogue. The prompt explicitly asks for
disfluencies, cut-ins and genuine reactions so the result feels like two people
talking rather than alternating monologues -- this is the layer that stands in for
NotebookLM's conversational 'secret sauce'.

Output is parsed into structured ScriptLine objects (speaker A / B) so the audio
layer can assign distinct voices.
"""
from __future__ import annotations

import json

from ..config import settings
from ..models import Briefing, Script, ScriptLine, Topic


SYSTEM = (
    "You are a scriptwriter for a short, smart morning-learning podcast with two "
    "hosts who clearly enjoy each other's company. Host A ({a}) is the curious "
    "guide who frames topics; Host B ({b}) digs into detail and pushes back. "
    "Write natural spoken dialogue: contractions, occasional disfluencies "
    "(\"I mean\", \"right\", \"sort of\"), brief interjections, and real reactions. "
    "Avoid sounding scripted or like a lecture. Keep it substantive but light. "
    "Target about {minutes} minutes (~{words} words). Open with a quick hook, not "
    "\"welcome back\". Close with one memorable takeaway."
)

INSTRUCTION = (
    "Write the episode based on this briefing:\n\n<briefing>\n{briefing}\n</briefing>\n\n"
    "Return ONLY valid JSON, no markdown fences, of the form:\n"
    '{{"title": "...", "summary": "one-sentence summary", '
    '"lines": [{{"speaker": "A", "text": "..."}}, {{"speaker": "B", "text": "..."}}]}}'
)


class ScriptWriter:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.claude_model

    def write(self, topic: Topic, briefing: Briefing) -> Script:
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)
        words = settings.target_minutes * 150  # ~150 spoken wpm
        system = SYSTEM.format(
            a=settings.host_a_name,
            b=settings.host_b_name,
            minutes=settings.target_minutes,
            words=words,
        )
        resp = client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": INSTRUCTION.format(briefing=briefing.markdown)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = _safe_json(text)
        lines = [ScriptLine(speaker=l["speaker"], text=l["text"]) for l in data.get("lines", [])]
        return Script(
            topic_id=topic.id,
            title=data.get("title", topic.title),
            summary=data.get("summary", ""),
            lines=lines,
        )


def _safe_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise
