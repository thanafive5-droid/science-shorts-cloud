"""Real stock video fetcher: Pexels/Pixabay (optional) + free keyless Wikimedia Commons & NASA Video APIs."""

import logging
from pathlib import Path
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

USER_AGENT = "ScienceShortsCloud/1.0 (https://github.com)"


BLOCKED_WORDS = (
    "president", "senate", "congress", "parliament", "speech", "interview",
    "press", "ceremony", "election", "trump", "biden", "lecture", "presentation",
    "slide", "tutorial", "webinar", "panel", "briefing", "conference", "talk",
    "podcast", "news", "report", "logo", "intro", "title", "credits", "text",
    "chart", "graph", "diagram", "infographic", "document", "book", "screen",
    "recording", "wikipedia", "promo", "trailer", "announcement", "q&a",
)


def _relevance_score(text: str, query_terms: list[str]) -> int:
    """Count how many query keywords appear in the clip's title/description."""
    lower = text.lower()
    return sum(1 for term in query_terms if term in lower)


class MediaFetcher:
    """Downloads real HD stock video clips for each 2-3 second visual beat."""

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
            logger.warning("Pexels API key unauthorized (401); disabling Pexels and using free keyless sources.")
            self._pexels_disabled = True
            return []
        resp.raise_for_status()
        return resp.json().get("videos", [])

    async def _search_wikimedia(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search Wikimedia Commons for free public-domain/CC real videos (no API key needed)."""
        try:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrnamespace": "6",
                    "gsrsearch": f"filetype:video {query}",
                    "gsrlimit": "10",
                    "prop": "videoinfo",
                    "viprop": "url|mime|size",
                    "format": "json",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            scored: list[tuple[int, str, str]] = []
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            for page_id, page in pages.items():
                title_lower = str(page.get("title", "")).lower()
                if any(bad in title_lower for bad in BLOCKED_WORDS):
                    continue
                score = _relevance_score(title_lower, query_terms)
                if query_terms and score == 0:
                    continue
                info = (page.get("videoinfo") or [{}])[0]
                url = info.get("url")
                mime = info.get("mime", "")
                size = info.get("size") or 0
                if url and mime in ("video/webm", "video/mp4") and 150_000 < size < 45_000_000:
                    scored.append((score, f"wm_{page_id}", url))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [(vid, url) for _, vid, url in scored]
        except Exception as exc:
            logger.warning("Wikimedia search failed for '%s': %s", query, exc)
            return []

    async def _search_nasa(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search NASA Image & Video Library for real HD science/space videos (no API key needed)."""
        try:
            resp = await client.get(
                "https://images-api.nasa.gov/search",
                params={"q": query, "media_type": "video", "page_size": "8"},
                timeout=15.0,
            )
            resp.raise_for_status()
            items = resp.json().get("collection", {}).get("items", [])
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            candidates: list[tuple[int, str, str]] = []
            for item in items[:10]:
                meta = (item.get("data") or [{}])[0]
                nasa_id = meta.get("nasa_id")
                href = item.get("href")
                title_desc = f"{meta.get('title', '')} {meta.get('description', '')}".lower()
                if not nasa_id or not href:
                    continue
                if any(bad in title_desc for bad in BLOCKED_WORDS):
                    continue
                score = _relevance_score(title_desc, query_terms)
                if query_terms and score == 0:
                    continue
                candidates.append((score, nasa_id, href))

            candidates.sort(key=lambda x: x[0], reverse=True)
            results: list[tuple[str, str]] = []
            for _, nasa_id, href in candidates[:5]:
                assets_resp = await client.get(href, timeout=12.0)
                if assets_resp.status_code != 200:
                    continue
                assets = assets_resp.json()
                mp4s = [a for a in assets if isinstance(a, str) and a.endswith(".mp4")]
                # Prefer medium/mobile (~720p) for fast cloud download & encoding
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
        fallback_query: str = "science space nature",
    ) -> list[Path]:
        """Fetch one unique real video clip per beat query."""
        output_dir.mkdir(parents=True, exist_ok=True)
        used_ids: set[str] = set()
        downloaded: list[Path] = []

        async with httpx.AsyncClient() as client:
            for idx, query in enumerate(queries):
                url = await self._find_unique_clip_url(client, query, used_ids)

                if not url and " " in query:
                    url = await self._find_unique_clip_url(client, query.split()[0], used_ids)
                if not url:
                    url = await self._find_unique_clip_url(client, fallback_query, used_ids)
                if not url:
                    url = await self._find_unique_clip_url(client, "science", used_ids)

                if not url:
                    raise RuntimeError(f"Could not find any stock video for beat {idx}: '{query}'")

                ext = ".webm" if ".webm" in url.lower() else ".mp4"
                clip_path = output_dir / f"raw_{idx:02d}{ext}"
                await self._download_file(client, url, clip_path)
                downloaded.append(clip_path)

        logger.info("Downloaded %d unique real video clips", len(downloaded))
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

        # Free keyless fallback 1: Wikimedia Commons real videos
        for vid, url in await self._search_wikimedia(client, query):
            if vid not in used_ids:
                used_ids.add(vid)
                return url

        # Free keyless fallback 2: NASA Image & Video Library
        for vid, url in await self._search_nasa(client, query):
            if vid not in used_ids:
                used_ids.add(vid)
                return url

        return None
