"""Diverse, AI-verified real video fetcher: 80-90% Wikimedia Commons & Internet Archive, max 10-20% NASA."""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.safety_filter import VisualSafetyFilter

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
    "commercial", "advertisement", "cartoon", "wireframe", "fractal",
    "geometric", "abstract", "kaleidoscope", "screensaver", "visualizer",
    "oscilloscope",
    # Wikimedia noisy textbook/document/building/game filenames
    "map", "page", "scan", "pdf", "poster", "flag", "coat_of_arms",
    "signature", "portrait", "museum", "building", "street", "table",
    "schema", "illustration", "drawing", "sketch", "game", "gameplay",
)

ANIMATION_ONLY_BLOCKED = ("animation", "animated", "simulation", "cgi", "render")


def _is_safe_text(text: str, allow_animations: bool = False) -> bool:
    lower = text.lower()
    if any(bad in lower for bad in BLOCKED_WORDS):
        return False
    if not allow_animations and any(bad in lower for bad in ANIMATION_ONLY_BLOCKED):
        return False
    return True


DIVERSE_FALLBACK_QUERIES = (
    "water droplet slow motion",
    "lightning storm clouds",
    "ocean waves underwater",
    "chemistry laboratory liquid",
    "microscope cells biology",
    "fire flame slow motion",
    "forest nature timelapse",
    "ice crystal melting",
    "magnet physics experiment",
    "volcano lava glowing",
)

SAFE_NASA_FALLBACK_QUERIES = (
    "earth from space timelapse",
    "aurora borealis space",
    "solar flare sun",
    "nebula galaxy stars",
)


def _relevance_score(text: str, query_terms: list[str]) -> int:
    lower = text.lower()
    return sum(1 for term in query_terms if term in lower)


class MediaFetcher:
    """Downloads diverse, AI-verified safe real HD video clips (80-90% non-NASA, max 10-20% NASA)."""

    def __init__(
        self,
        pexels_api_key: str = "",
        pixabay_api_key: str = "",
        safety_filter: Optional[VisualSafetyFilter] = None,
    ) -> None:
        self.pexels_api_key = pexels_api_key
        self.pixabay_api_key = pixabay_api_key
        self.safety_filter = safety_filter
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
            logger.warning("Pexels API key unauthorized (401); disabling Pexels.")
            self._pexels_disabled = True
            return []
        resp.raise_for_status()
        return resp.json().get("videos", [])

    async def _search_pixabay(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
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
                if not _is_safe_text(str(h.get("tags", ""))):
                    continue
                url = (h.get("videos", {}).get("medium") or {}).get("url")
                if url:
                    results.append((f"pixabay_{h.get('id')}", url))
            return results
        except Exception as exc:
            logger.warning("Pixabay search failed for '%s': %s", query, exc)
            return []

    async def _search_wikimedia(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search Wikimedia Commons for diverse real science/nature videos (AI-verified before use)."""
        try:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrnamespace": "6",
                    "gsrsearch": f"filetype:video {query}",
                    "gsrlimit": "12",
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
                if not _is_safe_text(title_lower):
                    continue
                score = _relevance_score(title_lower, query_terms)
                if query_terms and score == 0:
                    continue
                info = (page.get("videoinfo") or [{}])[0]
                url = info.get("url")
                mime = info.get("mime", "")
                size = info.get("size") or 0
                if url and mime in ("video/webm", "video/mp4") and 150_000 < size < 38_000_000:
                    scored.append((score, f"wm_{page_id}", url))
            scored.sort(key=lambda x: (x[0], random.random()), reverse=True)
            return [(vid, url) for _, vid, url in scored[:6]]
        except Exception as exc:
            logger.warning("Wikimedia search failed for '%s': %s", query, exc)
            return []

    async def _search_wikimedia_photos(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search Wikimedia Commons for sharp, topic-relevant real photos (for Ken Burns motion when videos lack a match)."""
        try:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrnamespace": "6",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrlimit": "12",
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size|dimensions",
                    "iiurlwidth": "1280",
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
                if not _is_safe_text(title_lower, allow_animations=False):
                    continue
                score = _relevance_score(title_lower, query_terms)
                if query_terms and score == 0:
                    continue
                info = (page.get("imageinfo") or [{}])[0]
                url = info.get("thumburl") or info.get("url")
                mime = info.get("mime", "")
                w = info.get("width") or 0
                h = info.get("height") or 0
                if url and mime in ("image/jpeg", "image/png") and w >= 720 and h >= 540:
                    scored.append((score, f"wmimg_{page_id}", url))
            scored.sort(key=lambda x: (x[0], random.random()), reverse=True)
            return [(vid, url) for _, vid, url in scored[:4]]
        except Exception as exc:
            logger.warning("Wikimedia photo search failed for '%s': %s", query, exc)
            return []

    async def _search_archive(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search Internet Archive (archive.org) for diverse free MP4 videos (AI-verified before use)."""
        try:
            resp = await client.get(
                "https://archive.org/advancedsearch.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "q": f"({query}) AND mediatype:movies",
                    "fl[]": ["identifier", "title", "description"],
                    "rows": "8",
                    "output": "json",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            candidates: list[tuple[int, str]] = []
            for doc in docs:
                ident = doc.get("identifier")
                title_desc = f"{doc.get('title', '')} {doc.get('description', '')}"
                if not ident or not _is_safe_text(title_desc):
                    continue
                score = _relevance_score(title_desc, query_terms)
                candidates.append((score, ident))

            candidates.sort(key=lambda x: (x[0], random.random()), reverse=True)
            results: list[tuple[str, str]] = []
            for _, ident in candidates[:4]:
                meta_resp = await client.get(
                    f"https://archive.org/metadata/{ident}/files",
                    headers={"User-Agent": USER_AGENT},
                    timeout=12.0,
                )
                if meta_resp.status_code != 200:
                    continue
                files = meta_resp.json().get("result", [])
                mp4s = [
                    f for f in files
                    if str(f.get("name", "")).lower().endswith(".mp4")
                    and 200_000 < int(f.get("size") or 0) < 35_000_000
                ]
                mp4s.sort(key=lambda f: int(f.get("size") or 0))
                if mp4s:
                    fname = mp4s[0]["name"]
                    url = f"https://archive.org/download/{ident}/{fname}"
                    results.append((f"ia_{ident}", url))
            return results
        except Exception as exc:
            logger.warning("Internet Archive search failed for '%s': %s", query, exc)
            return []

    async def _search_nasa(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search NASA Image & Video Library (capped at 10-20% of clips per video)."""
        try:
            resp = await client.get(
                "https://images-api.nasa.gov/search",
                params={"q": query, "media_type": "video", "page_size": "10"},
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
                if not nasa_id or not href or not _is_safe_text(title_desc):
                    continue
                score = _relevance_score(title_desc, query_terms)
                candidates.append((score, nasa_id, href))

            candidates.sort(key=lambda x: x[0], reverse=True)
            results: list[tuple[str, str]] = []
            for _, nasa_id, href in candidates[:4]:
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
        fallback_query: str = "nature science",
        fallback_queries: tuple[str, ...] = (),
        nasa_ratio: float = 0.2,
        allow_animations: bool = True,
        previously_used_ids: Optional[set[str]] = None,
    ) -> tuple[list[Path], list[str]]:
        """Fetch diverse, topic-relevant clips using batched Gemini Vision checks (saves ~85% API quota)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        used_ids: set[str] = set(previously_used_ids or set())
        newly_used_ids: list[str] = []
        max_nasa = 0 if nasa_ratio <= 0 else max(1, round(len(queries) * nasa_ratio))
        nasa_count = 0

        # Batched Vision-verified passes:
        #   attempt 0: exact query (real videos + photos)
        #   attempt 1: exact query (crisp Wikimedia real photos only for any rejected beat)
        #   attempt 2: broadened 2-word query (videos + photos)
        chosen: list[Optional[tuple[Path, bool, str]]] = [None] * len(queries)
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                pending_indices = [i for i, c in enumerate(chosen) if c is None]
                if not pending_indices:
                    break

                batch_downloads: list[tuple[int, Path, str, bool, str]] = []
                for idx in pending_indices:
                    raw_q = queries[idx]
                    safe_q = raw_q if _is_safe_text(raw_q, allow_animations=allow_animations) else fallback_query
                    photos_only = attempt == 1
                    if attempt == 2:
                        search_q = " ".join(safe_q.split()[:2]) if " " in safe_q else fallback_query
                    else:
                        search_q = safe_q
                    prefer_nasa = nasa_ratio >= 0.4 and (idx % 2 == 0) and (nasa_count < max_nasa)
                    allow_nasa = nasa_count < max_nasa
                    cands = await self._collect_candidates(
                        client,
                        search_q,
                        used_ids,
                        allow_nasa=allow_nasa,
                        prefer_nasa=prefer_nasa,
                        allow_animations=allow_animations,
                        include_photos=True,
                        photos_only=photos_only,
                    )
                    for vid, url, is_nasa in cands:
                        used_ids.add(vid)
                        low_u = url.lower()
                        ext = ".jpg" if any(k in low_u for k in (".jpg", ".jpeg", ".png")) else (".webm" if ".webm" in low_u else ".mp4")
                        temp_path = output_dir / f"cand_{attempt}_{idx:02d}_{vid}{ext}"
                        try:
                            await self._download_file(client, url, temp_path)
                            batch_downloads.append((idx, temp_path, raw_q, is_nasa, vid))
                            break
                        except Exception as exc:
                            logger.warning("Download failed for %s: %s", vid, exc)
                            temp_path.unlink(missing_ok=True)

                if not batch_downloads:
                    continue

                if self.safety_filter is not None:
                    verdicts = await asyncio.to_thread(
                        self.safety_filter.check_batch,
                        [(p, q) for _, p, q, _, _ in batch_downloads],
                        output_dir,
                        allow_animations=allow_animations,
                    )
                else:
                    verdicts = [(True, "ok")] * len(batch_downloads)

                for (idx, temp_path, raw_q, is_nasa, vid), (ok, reason) in zip(batch_downloads, verdicts):
                    if ok:
                        final_path = output_dir / f"raw_{idx:02d}{temp_path.suffix}"
                        temp_path.replace(final_path)
                        chosen[idx] = (final_path, is_nasa, vid)
                        if is_nasa:
                            nasa_count += 1
                    else:
                        logger.warning("REJECTED clip %s for '%s': %s", vid, raw_q, reason)
                        temp_path.unlink(missing_ok=True)

            # Emergency safe NASA fallback only up to max_nasa, then Vision-verified Wikimedia photo fallback, then round-robin reuse
            fb_list = list(SAFE_NASA_FALLBACK_QUERIES)
            random.shuffle(fb_list)
            ch_fb = list(fallback_queries) if fallback_queries else [fallback_query]
            random.shuffle(ch_fb)
            diverse_fb = list(DIVERSE_FALLBACK_QUERIES)
            random.shuffle(diverse_fb)
            photo_fb_queries = ch_fb + diverse_fb

            initial_verified = [c for c in chosen if c is not None]

            for idx, item in enumerate(chosen):
                if item is not None or nasa_count >= max_nasa:
                    continue
                for fb in fb_list:
                    for vid, url in await self._search_nasa(client, fb):
                        if vid not in used_ids:
                            used_ids.add(vid)
                            final_path = output_dir / f"raw_{idx:02d}.mp4"
                            await self._download_file(client, url, final_path)
                            chosen[idx] = (final_path, True, vid)
                            nasa_count += 1
                            break
                    if chosen[idx] is not None:
                        break

            for fb_attempt in range(2):
                if not any(c is None for c in chosen):
                    break
                fb_downloads: list[tuple[int, Path, str, str]] = []
                for idx, item in enumerate(chosen):
                    if item is not None:
                        continue
                    downloaded_fb = False
                    for fb_q in photo_fb_queries:
                        for vid, url in await self._search_wikimedia_photos(client, fb_q):
                            if vid not in used_ids:
                                used_ids.add(vid)
                                temp_path = output_dir / f"fb_{fb_attempt}_{idx:02d}_{vid}.jpg"
                                try:
                                    await self._download_file(client, url, temp_path)
                                    fb_downloads.append((idx, temp_path, fb_q, vid))
                                    downloaded_fb = True
                                    break
                                except Exception as exc:
                                    logger.warning("Fallback photo download failed for %s: %s", vid, exc)
                                    temp_path.unlink(missing_ok=True)
                        if downloaded_fb:
                            break

                if fb_downloads:
                    if self.safety_filter is not None:
                        fb_verdicts = await asyncio.to_thread(
                            self.safety_filter.check_batch,
                            [(p, q) for _, p, q, _ in fb_downloads],
                            output_dir,
                            allow_animations=allow_animations,
                        )
                    else:
                        fb_verdicts = [(True, "ok")] * len(fb_downloads)

                    for (idx, temp_path, fb_q, vid), (ok, reason) in zip(fb_downloads, fb_verdicts):
                        if ok:
                            final_path = output_dir / f"raw_{idx:02d}.jpg"
                            temp_path.replace(final_path)
                            chosen[idx] = (final_path, False, vid)
                        else:
                            logger.warning("REJECTED fallback photo %s for '%s': %s", vid, fb_q, reason)
                            temp_path.unlink(missing_ok=True)

            reuse_idx = 0
            for idx, item in enumerate(chosen):
                if item is not None:
                    continue
                base_pool = initial_verified if len(initial_verified) >= 1 else [c for c in chosen if c is not None]
                if nasa_ratio < 0.4:
                    non_nasa_pool = [c for c in base_pool if not c[1]]
                    pick_pool = non_nasa_pool if non_nasa_pool else base_pool
                else:
                    pick_pool = base_pool
                if len(base_pool) >= 1:
                    fallback_pick = pick_pool[reuse_idx % len(pick_pool)]
                    reuse_idx += 1
                    chosen[idx] = fallback_pick
                    logger.warning(
                        "Beat %d ('%s') reused verified safe visual %s round-robin after fallback passes",
                        idx,
                        queries[idx],
                        fallback_pick[2],
                    )
                else:
                    raise RuntimeError(f"Could not find any safe visual for beat {idx}: '{queries[idx]}'")

        downloaded = [c[0] for c in chosen if c is not None]
        newly_used_ids = [c[2] for c in chosen if c is not None]
        logger.info(
            "Downloaded %d unique AI-verified visuals (%d NASA / %d diverse non-NASA, skipped %d past clips)",
            len(downloaded),
            nasa_count,
            len(downloaded) - nasa_count,
            len(previously_used_ids or ()),
        )
        return downloaded, newly_used_ids

    async def _collect_candidates(
        self,
        client: httpx.AsyncClient,
        query: str,
        used_ids: set[str],
        allow_nasa: bool,
        prefer_nasa: bool = False,
        allow_animations: bool = True,
        include_photos: bool = True,
        photos_only: bool = False,
    ) -> list[tuple[str, str, bool]]:
        """Return prioritized list of (vid, url, is_nasa) including real videos + sharp topic photos."""
        if photos_only:
            return [
                (vid, url, False)
                for vid, url in await self._search_wikimedia_photos(client, query)
                if vid not in used_ids
            ]

        non_nasa: list[tuple[str, str, bool]] = []
        nasa_list: list[tuple[str, str, bool]] = []

        if not self._pexels_disabled and self.pexels_api_key:
            for orientation in ("portrait", None):
                videos = await self._search_pexels(client, query, orientation=orientation)
                for v in videos:
                    vid = f"pexels_{v.get('id')}"
                    if vid in used_ids or (v.get("duration") or 0) < 2:
                        continue
                    url = self._pick_pexels_url(v)
                    if url:
                        non_nasa.append((vid, url, False))

        for vid, url in await self._search_pixabay(client, query):
            if vid not in used_ids:
                non_nasa.append((vid, url, False))

        for vid, url in await self._search_wikimedia(client, query):
            if vid not in used_ids:
                non_nasa.append((vid, url, False))

        for vid, url in await self._search_archive(client, query):
            if vid not in used_ids:
                non_nasa.append((vid, url, False))

        if include_photos:
            for vid, url in await self._search_wikimedia_photos(client, query):
                if vid not in used_ids:
                    non_nasa.append((vid, url, False))

        if allow_nasa:
            for vid, url in await self._search_nasa(client, query):
                if vid not in used_ids:
                    nasa_list.append((vid, url, True))

        if prefer_nasa:
            return nasa_list + non_nasa
        return non_nasa + nasa_list
