"""Podcast RSS feed generation.

Produces an iTunes-compatible RSS feed from published episodes, written to
data/feeds/feed.xml. Subscribe to {base_url}/feed.xml in any podcast app.

No external deps -- the feed is simple enough to build with the stdlib, which keeps
the local footprint tiny.
"""
from __future__ import annotations

from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from ..config import FEED_DIR, settings
from ..db import get_episodes


def build_feed(out_path: Path | None = None) -> Path:
    out_path = out_path or (FEED_DIR / "feed.xml")
    episodes = get_episodes()
    base = settings.base_url.rstrip("/")

    items = []
    for ep in episodes:
        audio_name = Path(ep.audio_path).name
        audio_url = f"{base}/audio/{audio_name}"
        size = Path(ep.audio_path).stat().st_size if Path(ep.audio_path).exists() else 0
        items.append(
            f"""    <item>
      <title>{escape(ep.title)}</title>
      <description>{escape(ep.summary)}</description>
      <enclosure url="{escape(audio_url)}" length="{size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{ep.id}</guid>
      <pubDate>{format_datetime(ep.published_at)}</pubDate>
      <itunes:duration>{ep.duration_seconds}</itunes:duration>
    </item>"""
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(settings.feed_title)}</title>
    <link>{escape(base)}</link>
    <description>{escape(settings.feed_description)}</description>
    <language>en-au</language>
    <itunes:author>{escape(settings.feed_author)}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
{chr(10).join(items)}
  </channel>
</rss>
"""
    out_path.write_text(xml, encoding="utf-8")
    return out_path
