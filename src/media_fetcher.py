"""Real stock video fetcher: Pexels/Pixabay (optional) + free keyless Wikimedia Commons & NASA Video APIs."""

import logging
from pathlib import Path
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

USER_AGENT = "ScienceShortsCloud/1.0 (https://github.com)"


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
            results: list[tuple[str, str]] = []
            for page_id, page in pages.items():
                info = (page.get("videoinfo") or [{}])[0]
                url = info.get("url")
                mime = info.get("mime", "")
                size = info.get("size") or 0
                if url and mime in ("video/webm", "video/mp4") and 100_000 < size < 45_000_000:
                    results.append((f"wm_{page_id}", url))
            return results
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
            results: list[tuple[str, str]] = []
            for item in items[:5]:
                nasa_id = (item.get("data") or [{}])[0].get("nasa_id")
                href = item.get("href")
                if not nasa_id or not href:
                    continue
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
