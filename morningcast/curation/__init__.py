"""Curation engine.

Weekly job that proposes new topics by:
  1. Extracting recurring themes from the user's past selected topics.
  2. Pulling current affairs via Grok (Live Search).
  3. Having Claude propose 1-3 fresh, non-duplicate topics with a rationale.

Suggestions are saved with status=SUGGESTED and surfaced in the web UI for the
user to approve or reject -- the human stays in control of what gets produced.
"""
from __future__ import annotations

import json

from ..config import settings
from ..db import get_topics, save_topic
from ..models import Topic, TopicSource, TopicStatus
from ..research import GrokResearcher


def _past_titles() -> list[str]:
    seen = []
    for t in get_topics():
        seen.append(t.title)
    return seen


def suggest_topics(max_suggestions: int = 3) -> list[Topic]:
    past = _past_titles()

    # 1-2. Pull current affairs framed by the user's interests.
    grok = GrokResearcher()
    probe = Topic(
        title="Current affairs and notable developments relevant to these interests: "
        + "; ".join(past[:20] if past else ["general knowledge", "current affairs"]),
        notes="Curation probe -- surface fresh, noteworthy items from the last week.",
    )
    current = grok.gather(probe)

    # 3. Claude proposes concrete topics.
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    prompt = (
        "You curate topics for a short morning-learning podcast.\n\n"
        f"The listener has previously chosen these topics:\n{json.dumps(past, indent=2)}\n\n"
        f"Here is current-affairs material from a live search:\n<current>\n{current}\n</current>\n\n"
        f"Propose {max_suggestions} NEW topics worth a 6-minute episode. Mix themes that "
        "build on the listener's demonstrated interests with timely current-affairs items. "
        "Do not duplicate past topics. Each needs a one-line rationale.\n\n"
        'Return ONLY JSON: [{"title": "...", "rationale": "..."}]'
    )
    resp = client.messages.create(
        model=settings.claude_model,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    suggestions = _safe_json_list(text)

    created: list[Topic] = []
    for s in suggestions[:max_suggestions]:
        topic = Topic(
            title=s["title"],
            source=TopicSource.CURATED,
            status=TopicStatus.SUGGESTED,
            notes=s.get("rationale", ""),
        )
        save_topic(topic)
        created.append(topic)
    return created


def _safe_json_list(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        return []
