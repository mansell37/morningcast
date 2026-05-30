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


_SYSTEM_CASUAL = (
    "You are a scriptwriter for a short, smart morning-learning podcast with two "
    "hosts who clearly enjoy each other's company. Host A ({a}) is the curious "
    "guide who frames topics; Host B ({b}) digs into detail and pushes back. "
    "Write natural spoken dialogue: contractions, occasional disfluencies "
    "(\"I mean\", \"right\", \"sort of\"), brief interjections, and real reactions. "
    "Avoid sounding scripted or like a lecture. Keep it substantive but light. "
    "Target about {minutes} minutes (~{words} words). Open with a quick hook, not "
    "\"welcome back\". Close with one memorable takeaway."
)

_SYSTEM_DRY_BRITISH = (
    "You are a scriptwriter for a short, intelligent British-style podcast in the "
    "spirit of The Rest Is Politics or More or Less. Two hosts, Host A ({a}) and "
    "Host B ({b}), trade observations with dry, understated wit — deadpan asides, "
    "occasional sharp turns of phrase, mild self-deprecation. They take the subject "
    "seriously without taking themselves too seriously. Write natural spoken "
    "dialogue with British rhythms: \"well\", \"rather\", \"to be fair\", \"hang on\", "
    "\"the curious thing is\". Avoid Americanisms, exclamation points, and hard-sell "
    "openings. Open with a wry observation or a counterintuitive fact, not \"welcome "
    "back\". Target about {minutes} minutes (~{words} words). Close with a quietly "
    "memorable line — no fanfare."
)

_SYSTEM_NPR = (
    "You are a scriptwriter for a short, narrative-driven podcast in the style of "
    "NPR's Planet Money or Throughline. Host A ({a}) is the guide who frames the "
    "story; Host B ({b}) brings detail and the human angle. Use a clear narrative "
    "arc — hook, tension, surprise, resolution. Warm and professional, "
    "conversational but well-crafted. Vary cadence: short punchy lines, longer "
    "thoughtful ones. Target about {minutes} minutes (~{words} words). Open with a "
    "vivid moment or pointed question, not \"welcome back\". Close with a single "
    "image or idea that lingers."
)

_SYSTEM_ENERGETIC = (
    "You are a scriptwriter for an energetic morning-radio show. Two hosts, Host A "
    "({a}) and Host B ({b}), bring high energy and rapid-fire banter. Lots of "
    "reactions, interruptions, agreement chains, real laughter, callbacks within "
    "the episode. Substantive content delivered with enthusiasm — not shouty, just "
    "genuinely engaged. Target about {minutes} minutes (~{words} words). Open with "
    "a bold claim or a \"you'll never believe this\" moment. Close with a "
    "high-energy takeaway listeners can repeat to a friend."
)

STYLE_PRESETS: dict[str, dict] = {
    "dry_british": {"label": "Dry British wit (deadpan, observational)", "system": _SYSTEM_DRY_BRITISH},
    "casual":      {"label": "Smart but casual (two friends chatting)",  "system": _SYSTEM_CASUAL},
    "npr":         {"label": "NPR / Planet Money (polished narrative)",   "system": _SYSTEM_NPR},
    "energetic":   {"label": "Energetic morning radio (high energy)",     "system": _SYSTEM_ENERGETIC},
}

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

        from ..db import get_setting

        client = Anthropic(api_key=self.api_key)
        words = settings.target_minutes * 150  # ~150 spoken wpm
        preset_key = get_setting("style_preset", "dry_british")
        preset = STYLE_PRESETS.get(preset_key, STYLE_PRESETS["dry_british"])
        system = preset["system"].format(
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
