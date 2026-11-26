"""Lightweight, zero-quota YouTube Shorts view analyzer & prompt adaptation engine.

Fetches public Shorts view counts from each channel's /shorts tab (no OAuth scope or API quota
required) and builds a compact performance-feedback directive for ScriptGenerator, plus merges
per-channel manual strategy overrides from config/channel_strategies.json.
"""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import statistics
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

STRATEGIES_PATH = Path(__file__).resolve().parent.parent / "config" / "channel_strategies.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class ShortStat:
    video_id: str
    title: str
    views: int


@dataclass(frozen=True)
class ChannelPerformance:
    handle: str
    shorts: list[ShortStat]
    avg_views: float
    median_views: float
    recent_avg: float
    prev_avg: float
    trend: str  # "growing" | "stable" | "dropping" | "insufficient_data"
    top_shorts: list[ShortStat]
    bottom_shorts: list[ShortStat]


def _parse_view_count(text: str) -> int:
    """Parse strings like '1.2K views', '456 views', '3.4M views', 'No views' into int."""
    if not text:
        return 0
    low = text.lower().replace(",", "").strip()
    if "no view" in low:
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kmb])?\s*view", low)
    if not m:
        return 0
    val = float(m.group(1))
    suffix = m.group(2) or ""
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(round(val * mult))


def _walk_shorts_items(obj: Any, out: list[ShortStat], seen: set[str]) -> None:
    """Recursively extract Shorts lockup / reel items from ytInitialData."""
    if isinstance(obj, dict):
        # Modern YouTube Shorts grid uses shortsLockupViewModel
        if "shortsLockupViewModel" in obj:
            vm = obj["shortsLockupViewModel"]
            vid = (
                vm.get("onTap", {})
                .get("innertubeCommand", {})
                .get("reelWatchEndpoint", {})
                .get("videoId")
                or ""
            )
            overlay = vm.get("overlayMetadata", {})
            title = (overlay.get("primaryText", {}).get("content") or "").strip()
            views_txt = (overlay.get("secondaryText", {}).get("content") or "").strip()
            if not views_txt:
                views_txt = vm.get("accessibilityText", "")
            if vid and vid not in seen:
                seen.add(vid)
                out.append(ShortStat(video_id=vid, title=title, views=_parse_view_count(views_txt)))
            return
        # Legacy reelItemRenderer fallback
        if "reelItemRenderer" in obj:
            rr = obj["reelItemRenderer"]
            vid = rr.get("videoId") or ""
            title = (rr.get("headline", {}).get("simpleText") or "").strip()
            views_txt = (rr.get("viewCountText", {}).get("simpleText") or "").strip()
            if vid and vid not in seen:
                seen.add(vid)
                out.append(ShortStat(video_id=vid, title=title, views=_parse_view_count(views_txt)))
            return
        for v in obj.values():
            _walk_shorts_items(v, out, seen)
    elif isinstance(obj, list):
        for item in obj:
            _walk_shorts_items(item, out, seen)


def fetch_channel_shorts(youtube_handle: str, limit: int = 20) -> list[ShortStat]:
    """Fetch recent public Shorts and view counts for a YouTube @handle (0 API quota)."""
    if not youtube_handle:
        return []
    handle = youtube_handle if youtube_handle.startswith("@") else f"@{youtube_handle}"
    url = f"https://www.youtube.com/{handle}/shorts"
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=12.0) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
        if not m:
            logger.warning("ytInitialData not found for %s", handle)
            return []
        data = json.loads(m.group(1))
        items: list[ShortStat] = []
        _walk_shorts_items(data, items, set())
        return items[:limit]
    except Exception as exc:
        logger.warning("Could not fetch Shorts views for %s: %s", handle, exc)
        return []


def analyze_channel_performance(youtube_handle: str, limit: int = 20) -> ChannelPerformance:
    """Compute view statistics, trend, and top/bottom Shorts for a channel."""
    shorts = fetch_channel_shorts(youtube_handle, limit=limit)
    if not shorts:
        return ChannelPerformance(
            handle=youtube_handle,
            shorts=[],
            avg_views=0.0,
            median_views=0.0,
            recent_avg=0.0,
            prev_avg=0.0,
            trend="insufficient_data",
            top_shorts=[],
            bottom_shorts=[],
        )

    views = [s.views for s in shorts]
    avg_v = sum(views) / len(views)
    med_v = float(statistics.median(views))

    # Skip the very newest clip (index 0) if we have >= 7 clips, since it may still be seeding
    sample = shorts[1:] if len(shorts) >= 7 else shorts
    if len(sample) >= 6:
        recent = sample[:3]
        prev = sample[3:8]
        recent_avg = sum(s.views for s in recent) / len(recent)
        prev_avg = sum(s.views for s in prev) / len(prev)
        if prev_avg >= 50 and recent_avg < prev_avg * 0.72:
            trend = "dropping"
        elif prev_avg >= 50 and recent_avg > prev_avg * 1.25:
            trend = "growing"
        else:
            trend = "stable"
    else:
        recent_avg = avg_v
        prev_avg = avg_v
        trend = "insufficient_data"

    ranked = sorted(sample, key=lambda s: s.views, reverse=True)
    top_shorts = [s for s in ranked[:3] if s.views > 0]
    bottom_shorts = ranked[-3:] if len(ranked) >= 6 else []

    return ChannelPerformance(
        handle=youtube_handle,
        shorts=shorts,
        avg_views=round(avg_v, 1),
        median_views=round(med_v, 1),
        recent_avg=round(recent_avg, 1),
        prev_avg=round(prev_avg, 1),
        trend=trend,
        top_shorts=top_shorts,
        bottom_shorts=bottom_shorts,
    )


def load_channel_strategy(channel_id: str) -> dict[str, Any]:
    """Load optional manual adaptation overrides for a channel from config/channel_strategies.json."""
    if not STRATEGIES_PATH.exists():
        return {}
    try:
        data = json.loads(STRATEGIES_PATH.read_text(encoding="utf-8"))
        return data.get(channel_id, {}) if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load channel_strategies.json: %s", exc)
        return {}


def build_adaptation_context(channel_id: str, youtube_handle: str) -> tuple[str, list[str]]:
    """Return (adaptation_prompt_block, published_short_titles) from one live channel fetch."""
    lines: list[str] = []

    perf = analyze_channel_performance(youtube_handle, limit=60)
    published_titles = [s.title for s in perf.shorts if s.title]
    if perf.top_shorts:
        top_str = "; ".join(f'"{s.title}" ({s.views:,} views)' for s in perf.top_shorts)
        lines.append(f"- TOP PERFORMING SHORTS on this channel: {top_str}.")
        lines.append(
            "- Lean into similar curiosity-gap framing, concrete surprising twists, and punchy title structures."
        )
    if perf.bottom_shorts:
        low_str = "; ".join(f'"{s.title}" ({s.views:,} views)' for s in perf.bottom_shorts)
        lines.append(f"- LOWER PERFORMING SHORTS: {low_str}. Avoid overly textbook or predictable angles like these.")
    if perf.trend == "dropping":
        lines.append(
            f"- RECOVERY ADAPTATION ACTIVE (recent avg {perf.recent_avg:.0f} vs prior {perf.prev_avg:.0f} views): "
            "Use an extra-strong pattern-interrupt opening in the first 1.5 seconds, raise the stakes immediately, "
            "and pick a high-curiosity visual phenomenon that makes viewers stop scrolling."
        )

    strat = load_channel_strategy(channel_id)
    if strat.get("focus_topics"):
        lines.append(f"- PRIORITY SUB-TOPICS: {', '.join(strat['focus_topics'])}.")
    if strat.get("avoid_angles"):
        lines.append(f"- AVOID ANGLES: {', '.join(strat['avoid_angles'])}.")
    if strat.get("hook_style") and strat["hook_style"] != "default":
        lines.append(f"- PREFERRED HOOK STYLE: {strat['hook_style']}.")
    if strat.get("extra_directive"):
        lines.append(f"- CHANNEL DIRECTIVE: {strat['extra_directive']}")

    if not lines:
        return "", published_titles
    logger.info(
        "View adaptation for [%s]: %d Shorts analyzed, median=%.0f, trend=%s",
        channel_id,
        len(perf.shorts),
        perf.median_views,
        perf.trend,
    )
    return (
        "LIVE AUDIENCE VIEW ADAPTATION (optimize for retention & views):\n" + "\n".join(lines) + "\n",
        published_titles,
    )


def build_adaptation_prompt_block(channel_id: str, youtube_handle: str) -> str:
    """Build a concise prompt block combining live YouTube view feedback + manual strategy."""
    return build_adaptation_context(channel_id, youtube_handle)[0]
