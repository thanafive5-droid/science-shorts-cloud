"""Real stock video fetcher using Pexels (primary) and Pixabay (fallback)."""

import logging
from pathlib import Path
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class MediaFetcher:
    """Downloads real HD stock video clips for each 2-3 second visual beat."""

    def __init__(self, pexels_api_key: str, pixabay_api_key: str = "") -> None:
        if not pexels_api_key and not pixabay_api_key:
            raise ValueError("At least one of PEXELS_API_KEY or PIXABAY_API_KEY is required")
        self.pexels_api_key = pexels_api_key
        self.pixabay_api_key = pixabay_api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
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
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json().get("videos", [])

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=6))
    async def _search_pixabay(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        resp = await client.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.pixabay_api_key, "q": query, "per_page": 10},
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json().get("hits", [])

    @staticmethod
    def _pick_pexels_url(video_obj: dict) -> Optional[str]:
        files = video_obj.get("video_files", [])
        mp4s = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
        if not mp4s:
            return None
        # Prefer 720p-1080p for fast cloud download & low-RAM FFmpeg processing
        mp4s.sort(key=lambda f: abs((f.get("height") or 720) - 1080))
        return mp4s[0]["link"]

    @staticmethod
    def _pick_pixabay_url(hit: dict) -> Optional[str]:
        videos = hit.get("videos", {})
        for quality in ("medium", "large", "small"):
            url = videos.get(quality, {}).get("url")
            if url:
                return url
        return None

    async def _download_file(self, client: httpx.AsyncClient, url: str, dest: Path) -> Path:
        async with client.stream("GET", url, timeout=45.0, follow_redirects=True) as resp:
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
                clip_path = output_dir / f"raw_{idx:02d}.mp4"
                url = await self._find_unique_clip_url(client, query, used_ids)

                if not url and " " in query:
                    url = await self._find_unique_clip_url(client, query.split()[0], used_ids)
                if not url:
                    url = await self._find_unique_clip_url(client, fallback_query, used_ids)

                if not url:
                    raise RuntimeError(f"Could not find any stock video for beat {idx}: '{query}'")

                await self._download_file(client, url, clip_path)
                downloaded.append(clip_path)

        logger.info("Downloaded %d unique real video clips", len(downloaded))
        return downloaded

    async def _find_unique_clip_url(
        self, client: httpx.AsyncClient, query: str, used_ids: set[str]
    ) -> Optional[str]:
        if self.pexels_api_key:
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

        if self.pixabay_api_key:
            hits = await self._search_pixabay(client, query)
            for h in hits:
                vid = f"pixabay_{h.get('id')}"
                if vid in used_ids or (h.get("duration") or 0) < 2:
                    continue
                url = self._pick_pixabay_url(h)
                if url:
                    used_ids.add(vid)
                    return url

        return None
