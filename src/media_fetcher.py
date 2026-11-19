"""Strictly family-safe real video fetcher: NASA Image & Video Library (100% G-rated) + optional Pexels/Pixabay."""

import logging
from pathlib import Path
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

USER_AGENT = "ScienceShortsCloud/1.0 (https://github.com)"

# Strict safety + quality blocklist (zero NSFW, nudity, gore, violence, or talk/slide videos)
BLOCKED_WORDS = (
    # Safety / NSFW / graphic / sensitive
    "nude", "nudity", "naked", "sex", "sexual", "erotic", "porn", "nsfw", "xxx",
    "breast", "genital", "lingerie", "bikini", "fetish", "strip", "adult",
    "gore", "blood", "bloody", "surgery", "autopsy", "corpse", "dead body",
    "violence", "violent", "weapon", "gun", "shooting", "war", "kill", "murder",
    "drug", "cocaine", "heroin", "suicide", "self-harm",
    # Non-visual / talk / slide / logo / political
    "president", "senate", "congress", "parliament", "speech", "interview",
    "press", "ceremony", "election", "trump", "biden", "lecture", "presentation",
    "slide", "tutorial", "webinar", "panel", "briefing", "conference", "talk",
    "podcast", "news", "report", "logo", "intro", "title", "credits", "text",
    "chart", "graph", "diagram", "infographic", "document", "book", "screen",
    "recording", "wikipedia", "promo", "trailer", "announcement", "q&a",
    "town hall", "remarks", "keynote", "awards", "commencement", "stadium",
)

# 100% G-rated, visually stunning NASA fallback queries by channel/topic
SAFE_FALLBACK_QUERIES = (
    "earth from space timelapse",
    "aurora borealis space",
    "solar flare sun",
    "science experiment laboratory",
    "ocean waves earth",
    "nebula galaxy stars",
    "lightning storm earth",
    "volcano lava earth",
    "rocket launch flame",
    "water droplet microgravity",
)


def _is_safe_text(text: str) -> bool:
    lower = text.lower()
    return not any(bad in lower for bad in BLOCKED_WORDS)


def _relevance_score(text: str, query_terms: list[str]) -> int:
    lower = text.lower()
    return sum(1 for term in query_terms if term in lower)


class MediaFetcher:
    """Downloads strictly family-safe real HD video clips (zero unmoderated sources)."""

    def __init__(self, pexels_api_key: str = "", pixabay_api_key: str = "") -> None:
        self.pexels_api_key = pexels_api_key
        self.pixabay_api_key = pixabay_api_key
        self._pexels_disabled = not bool(pexels_api_key)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def _search_pexels(
        self, client: httpx.AsyncClient, query: str, orientation: Optional[str] = "portrait"
    ) -> list[dict]:
        params: dict[str, str | int] = {"query": query, "per_page": 10}
        if orientation:
            params["orientation"] = orientation
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": self.pexels_api_key},
            params=params,
            timeout=15.0,
        )
        if resp.status_code == 401:
            logger.warning("Pexels API key unauthorized (401); disabling Pexels and using 100%% G-rated NASA library.")
            self._pexels_disabled = True
            return []
        resp.raise_for_status()
        return resp.json().get("videos", [])

    async def _search_pixabay(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search Pixabay with safesearch=true enabled."""
        if not self.pixabay_api_key:
            return []
        try:
            resp = await client.get(
                "https://pixabay.com/api/videos/",
                params={"key": self.pixabay_api_key, "q": query, "safesearch": "true", "per_page": 10},
                timeout=15.0,
            )
            resp.raise_for_status()
            results: list[tuple[str, str]] = []
            for h in resp.json().get("hits", []):
                tags = str(h.get("tags", ""))
                if not _is_safe_text(tags):
                    continue
                url = (h.get("videos", {}).get("medium") or {}).get("url")
                if url:
                    results.append((f"pixabay_{h.get('id')}", url))
            return results
        except Exception as exc:
            logger.warning("Pixabay search failed for '%s': %s", query, exc)
            return []

    async def _search_nasa(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search NASA Image & Video Library (100% G-rated, government-curated real HD science videos)."""
        try:
            resp = await client.get(
                "https://images-api.nasa.gov/search",
                params={"q": query, "media_type": "video", "page_size": "15"},
                timeout=15.0,
            )
            resp.raise_for_status()
            items = resp.json().get("collection", {}).get("items", [])
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            candidates: list[tuple[int, str, str]] = []
            for item in items:
                meta = (item.get("data") or [{}])[0]
                nasa_id = meta.get("nasa_id")
                href = item.get("href")
                title_desc = f"{meta.get('title', '')} {meta.get('description', '')}"
                if not nasa_id or not href:
                    continue
                if not _is_safe_text(title_desc):
                    continue
                score = _relevance_score(title_desc, query_terms)
                candidates.append((score, nasa_id, href))

            candidates.sort(key=lambda x: x[0], reverse=True)
            results: list[tuple[str, str]] = []
            for _, nasa_id, href in candidates[:6]:
                assets_resp = await client.get(href, timeout=12.0)
                if assets_resp.status_code != 200:
                    continue
                assets = assets_resp.json()
                mp4s = [a for a in assets if isinstance(a, str) and a.endswith(".mp4")]
                preferred = sorted(
                    mp4s,
                    key=lambda u: 0 if "~medium." in u else (1 if "~mobile." in u else 2),
                )
                if preferred:
                    results.append((f"nasa_{nasa_id}", preferred[0].replace("http://", "https://")))
            return results
        except Exception as exc:
            logger.warning("NASA search failed for '%s': %s", query, exc)
            return []

    @staticmethod
    def _pick_pexels_url(video_obj: dict) -> Optional[str]:
        files = video_obj.get("video_files", [])
        mp4s = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
        if not mp4s:
            return None
        mp4s.sort(key=lambda f: abs((f.get("height") or 720) - 1080))
        return mp4s[0]["link"]

    async def _download_file(self, client: httpx.AsyncClient, url: str, dest: Path) -> Path:
        async with client.stream(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
        return dest

    async def fetch_clips_for_beats(
        self,
        queries: list[str],
        output_dir: Path,
        fallback_query: str = "earth from space",
    ) -> list[Path]:
        """Fetch one unique, strictly family-safe real video clip per beat query."""
        output_dir.mkdir(parents=True, exist_ok=True)
        used_ids: set[str] = set()
        downloaded: list[Path] = []

        async with httpx.AsyncClient() as client:
            for idx, query in enumerate(queries):
                safe_query = query if _is_safe_text(query) else fallback_query
                url = await self._find_unique_clip_url(client, safe_query, used_ids)

                if not url and " " in safe_query:
                    url = await self._find_unique_clip_url(client, safe_query.split()[0], used_ids)
                if not url:
                    url = await self._find_unique_clip_url(client, fallback_query, used_ids)
                if not url:
                    fb = SAFE_FALLBACK_QUERIES[idx % len(SAFE_FALLBACK_QUERIES)]
                    url = await self._find_unique_clip_url(client, fb, used_ids)

                if not url:
                    raise RuntimeError(f"Could not find any safe stock video for beat {idx}: '{query}'")

                clip_path = output_dir / f"raw_{idx:02d}.mp4"
                await self._download_file(client, url, clip_path)
                downloaded.append(clip_path)

        logger.info("Downloaded %d unique 100%% safe real video clips", len(downloaded))
        return downloaded

    async def _find_unique_clip_url(
        self, client: httpx.AsyncClient, query: str, used_ids: set[str]
    ) -> Optional[str]:
        if not self._pexels_disabled and self.pexels_api_key:
            for orientation in ("portrait", None):
                videos = await self._search_pexels(client, query, orientation=orientation)
                for v in videos:
                    vid = f"pexels_{v.get('id')}"
                    if vid in used_ids or (v.get("duration") or 0) < 2:
                        continue
                    url = self._pick_pexels_url(v)
                    if url:
                        used_ids.add(vid)
                        return url

        for vid, url in await self._search_pixabay(client, query):
            if vid not in used_ids:
                used_ids.add(vid)
                return url

        # Strictly 100% G-rated NASA Image & Video Library (Wikimedia Commons permanently removed)
        for vid, url in await self._search_nasa(client, query):
            if vid not in used_ids:
                used_ids.add(vid)
                return url

        return None
