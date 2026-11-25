"""Diverse, AI-verified real video & photo fetcher: Wikimedia, Wikipedia, Openverse, Archive, NASA."""

import asyncio
import logging
import math
import random
from pathlib import Path
from typing import Optional
import httpx

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
    # Wikimedia / Wikipedia noisy textbook/document/building/game/diagram filenames
    "map", "page", "scan", "pdf", "poster", "flag", "coat_of_arms",
    "signature", "portrait", "museum", "building", "street", "table",
    "schema", "schematic", "illustration", "drawing", "sketch", "game", "gameplay",
    "collage", "holotype", "specimen", "distribution", "range_map", "radar",
    "screenshot", "settings", "menu", "icon", "button", "packaging", "board_game",
    "cruise", "ship_port", "reproductive",
    # Captive/food/vintage noise
    "cage", "zoo", "captive", "seafood", "chopped", "cooked", "recipe",
    "food", "meal", "dish", "vintage", "menu", "restaurant", "market",
)

ANIMATION_ONLY_BLOCKED = ("animation", "animated", "simulation", "cgi", "render")

FILLER_QUERY_WORDS = frozenset({
    "dark", "endless", "giant", "tiny", "super", "extreme", "close", "closeup",
    "macro", "slow", "motion", "style", "view", "camera", "x-ray", "xray",
    "animated", "animation", "dramatic", "real", "footage", "high", "ultra",
    "the", "and", "with", "from", "into", "over", "under", "very", "most",
})

SAFE_NASA_FALLBACK_QUERIES = (
    "earth from space timelapse",
    "aurora borealis space",
    "solar flare sun",
    "nebula galaxy stars",
)


def _is_safe_text(text: str, allow_animations: bool = False) -> bool:
    lower = text.lower()
    if any(bad in lower for bad in BLOCKED_WORDS):
        return False
    if not allow_animations and any(bad in lower for bad in ANIMATION_ONLY_BLOCKED):
        return False
    return True


def _relevance_score(text: str, query_terms: list[str]) -> int:
    lower = text.lower()
    return sum(1 for term in query_terms if term in lower)


def _simplify_query(query: str, max_terms: int = 3) -> str:
    """Strip poetic/camera filler adjectives while preserving core concrete subject/habitat nouns."""
    words = [w.strip(".,!?;:\"'") for w in query.lower().split()]
    kept = [w for w in words if len(w) > 2 and w not in FILLER_QUERY_WORDS]
    if not kept:
        kept = [w for w in words if len(w) > 2][:max_terms]
    return " ".join(kept[:max_terms]) or query.strip()


class MediaFetcher:
    """Downloads diverse, AI-verified safe real HD clips & sharp topic-matched photos."""

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
        self._openverse_failures = 0

    async def _search_pexels(
        self, client: httpx.AsyncClient, query: str, orientation: Optional[str] = "portrait"
    ) -> list[dict]:
        if self._pexels_disabled or not self.pexels_api_key:
            return []
        params: dict[str, str | int] = {"query": query, "per_page": 10}
        if orientation:
            params["orientation"] = orientation
        try:
            resp = await client.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": self.pexels_api_key},
                params=params,
                timeout=10.0,
            )
            if resp.status_code == 401:
                logger.info("Pexels API key rejected (401); disabling Pexels for this run.")
                self._pexels_disabled = True
                return []
            resp.raise_for_status()
            return resp.json().get("videos", [])
        except Exception as exc:
            logger.warning("Pexels search failed for '%s': %s", query, exc)
            return []

    async def _search_pixabay(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        if not self.pixabay_api_key:
            return []
        try:
            resp = await client.get(
                "https://pixabay.com/api/videos/",
                params={"key": self.pixabay_api_key, "q": query, "safesearch": "true", "per_page": 10},
                timeout=12.0,
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
                timeout=12.0,
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
        """Search Wikimedia Commons for sharp, topic-relevant real photos."""
        try:
            resp = await client.get(
                "https://commons.wikimedia.org/w/api.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrnamespace": "6",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrlimit": "30",
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size|dimensions",
                    "iiurlwidth": "1280",
                    "format": "json",
                },
                timeout=12.0,
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
            return [(vid, url) for _, vid, url in scored[:12]]
        except Exception as exc:
            logger.warning("Wikimedia photo search failed for '%s': %s", query, exc)
            return []

    async def _search_wikipedia_images(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search English Wikipedia article lead images for highly reliable, topic-accurate photography."""
        try:
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                headers={"User-Agent": USER_AGENT},
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrlimit": "8",
                    "prop": "pageimages",
                    "piprop": "original|thumbnail",
                    "pithumbsize": "1280",
                    "format": "json",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            scored: list[tuple[int, str, str]] = []
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            for page_id, page in pages.items():
                title_lower = str(page.get("title", "")).lower()
                if not _is_safe_text(title_lower, allow_animations=False):
                    continue
                thumb = page.get("thumbnail") or page.get("original") or {}
                url = str(thumb.get("source") or "")
                low_url = url.lower()
                if not url or not any(ext in low_url for ext in (".jpg", ".jpeg", ".png")):
                    continue
                if not _is_safe_text(low_url, allow_animations=False):
                    continue
                w = thumb.get("width") or 0
                h = thumb.get("height") or 0
                if w < 640 or h < 420:
                    continue
                score = _relevance_score(f"{title_lower} {low_url}", query_terms)
                if query_terms and score == 0:
                    continue
                scored.append((score, f"wpimg_{page_id}", url))
            scored.sort(key=lambda x: (x[0], random.random()), reverse=True)
            return [(vid, url) for _, vid, url in scored[:6]]
        except Exception as exc:
            logger.warning("Wikipedia image search failed for '%s': %s", query, exc)
            return []

    async def _search_openverse_photos(self, client: httpx.AsyncClient, query: str) -> list[tuple[str, str]]:
        """Search keyless Openverse CC photo API with fast timeout and per-job circuit breaker."""
        if self._openverse_failures >= 3:
            return []
        try:
            resp = await client.get(
                "https://api.openverse.org/v1/images/",
                headers={"User-Agent": USER_AGENT},
                params={
                    "q": query,
                    "category": "photograph",
                    "mature": "false",
                    "page_size": "15",
                },
                timeout=8.0,
            )
            resp.raise_for_status()
            items = resp.json().get("results", [])
            scored: list[tuple[int, str, str]] = []
            query_terms = [t.lower() for t in query.split() if len(t) > 2]
            for item in items:
                oid = item.get("id")
                title = str(item.get("title") or "")
                if not oid or not _is_safe_text(title, allow_animations=False):
                    continue
                w = item.get("width") or 0
                h = item.get("height") or 0
                if w < 720 or h < 540:
                    continue
                raw_url = str(item.get("url") or "")
                thumb_url = str(item.get("thumbnail") or "")
                low_url = raw_url.lower()
                if any(ext in low_url for ext in (".jpg", ".jpeg", ".png")):
                    img_url = raw_url
                elif thumb_url.startswith("http"):
                    img_url = thumb_url
                else:
                    continue
                score = _relevance_score(title, query_terms)
                if query_terms and score == 0:
                    continue
                scored.append((score, str(oid), img_url))
            scored.sort(key=lambda x: (x[0], random.random()), reverse=True)
            return [(f"ov_{oid}", img_url) for _, oid, img_url in scored[:8]]
        except Exception as exc:
            self._openverse_failures += 1
            if self._openverse_failures >= 3:
                logger.warning(
                    "Openverse failed %d times (%s); tripping circuit breaker for this job.",
                    self._openverse_failures,
                    exc,
                )
            else:
                logger.warning("Openverse photo search failed for '%s': %s", query, exc)
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
                timeout=12.0,
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
                    timeout=10.0,
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
        """Search NASA Image & Video Library (used ONLY for Astronomy up to max_nasa)."""
        try:
            resp = await client.get(
                "https://images-api.nasa.gov/search",
                params={"q": query, "media_type": "video", "page_size": "10"},
                timeout=12.0,
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
                assets_resp = await client.get(href, timeout=10.0)
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
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=45.0, follow_redirects=True
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
        video_topic: str = "",
        topic_fallback_queries: list[str] | tuple[str, ...] = (),
        target_duration_sec: float = 0.0,
        cut_max_sec: float = 2.5,
    ) -> tuple[list[Path], list[str]]:
        """Fetch diverse, topic-anchored unique visuals so every cut fits cut_max_sec with zero repeats."""
        output_dir.mkdir(parents=True, exist_ok=True)
        self._openverse_failures = 0

        # Ensure enough beat slots so average cut length never exceeds cut_max_sec (e.g. 2.5s)
        min_required = (
            max(6, math.ceil(target_duration_sec / max(1.0, cut_max_sec)))
            if target_duration_sec > 0
            else min(len(queries), 6)
        )
        working_queries = list(queries)
        extra_pool = [q for q in topic_fallback_queries if q]
        extra_idx = 0
        while len(working_queries) < min_required and extra_pool:
            working_queries.append(extra_pool[extra_idx % len(extra_pool)])
            extra_idx += 1

        used_ids: set[str] = set(previously_used_ids or set())
        max_nasa = 0 if nasa_ratio <= 0 else max(1, round(len(working_queries) * nasa_ratio))
        nasa_count = 0

        # Batched Vision-verified passes anchored to video_topic:
        #   attempt 0: exact query (videos + photos)
        #   attempt 1: exact query (Wikimedia + Wikipedia + Openverse sharp photos)
        #   attempt 2: simplified concrete noun query (videos + photos)
        chosen: list[Optional[tuple[Path, bool, str, bool]]] = [None] * len(working_queries)
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                pending_indices = [i for i, c in enumerate(chosen) if c is None]
                if not pending_indices:
                    break

                batch_downloads: list[tuple[int, Path, str, bool, str]] = []
                for idx in pending_indices:
                    raw_q = working_queries[idx]
                    safe_q = raw_q if _is_safe_text(raw_q, allow_animations=allow_animations) else fallback_query
                    photos_only = attempt == 1
                    search_q = _simplify_query(safe_q) if attempt == 2 else safe_q
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
                        video_topic=video_topic,
                    )
                else:
                    verdicts = [(True, "ok")] * len(batch_downloads)

                for (idx, temp_path, raw_q, is_nasa, vid), (ok, reason) in zip(batch_downloads, verdicts):
                    if ok:
                        final_path = output_dir / f"raw_{idx:02d}{temp_path.suffix}"
                        temp_path.replace(final_path)
                        chosen[idx] = (final_path, is_nasa, vid, True)
                        if is_nasa:
                            nasa_count += 1
                    else:
                        logger.warning("REJECTED clip %s for '%s': %s", vid, raw_q, reason)
                        temp_path.unlink(missing_ok=True)

            # Emergency safe NASA fallback only up to max_nasa (Astronomy only)
            fb_list = list(SAFE_NASA_FALLBACK_QUERIES)
            random.shuffle(fb_list)
            for idx, item in enumerate(chosen):
                if item is not None or nasa_count >= max_nasa:
                    continue
                for fb in fb_list:
                    for vid, url in await self._search_nasa(client, fb):
                        if vid not in used_ids:
                            used_ids.add(vid)
                            final_path = output_dir / f"raw_{idx:02d}.mp4"
                            await self._download_file(client, url, final_path)
                            chosen[idx] = (final_path, True, vid, False)
                            nasa_count += 1
                            break
                    if chosen[idx] is not None:
                        break

            # Stage A: Same-topic fallback pool ONLY (never off-topic habitats/subjects!)
            topic_fb_pool: list[str] = []
            seen_fb: set[str] = set()
            for candidate_q in [
                *topic_fallback_queries,
                _simplify_query(video_topic, max_terms=4) if video_topic else "",
                *[_simplify_query(q, max_terms=2) for q in working_queries],
            ]:
                cq = str(candidate_q).strip()
                if cq and cq.lower() not in seen_fb and _is_safe_text(cq, allow_animations=False):
                    seen_fb.add(cq.lower())
                    topic_fb_pool.append(cq)

            # Stage B: Channel fallback queries as last resort ONLY if still short of min_required
            channel_fb_pool = [
                q for q in (list(fallback_queries) if fallback_queries else [fallback_query])
                if q and q.lower() not in seen_fb
            ]

            for stage_name, fb_pool in (("topic", topic_fb_pool), ("channel", channel_fb_pool)):
                if not fb_pool:
                    continue
                current_ready = sum(1 for c in chosen if c is not None)
                if stage_name == "channel" and current_ready >= min_required:
                    break
                if not any(c is None for c in chosen):
                    break

                max_fb_attempts = 2 if stage_name == "topic" else 1
                for fb_attempt in range(max_fb_attempts):
                    if not any(c is None for c in chosen):
                        break
                    fb_downloads: list[tuple[int, Path, str, str]] = []
                    for idx, item in enumerate(chosen):
                        if item is not None:
                            continue
                        offset = (idx + fb_attempt * 2) % len(fb_pool)
                        rotated_queries = fb_pool[offset:] + fb_pool[:offset]
                        downloaded_fb = False
                        for fb_q in rotated_queries:
                            wm_cands = await self._search_wikimedia_photos(client, fb_q)
                            wp_cands = await self._search_wikipedia_images(client, fb_q)
                            ov_cands = await self._search_openverse_photos(client, fb_q)
                            for vid, url in [*wm_cands, *wp_cands, *ov_cands]:
                                if vid not in used_ids:
                                    used_ids.add(vid)
                                    temp_path = output_dir / f"fb_{stage_name}_{fb_attempt}_{idx:02d}_{vid}.jpg"
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
                                video_topic=video_topic,
                            )
                        else:
                            fb_verdicts = [(True, "ok")] * len(fb_downloads)

                        for (idx, temp_path, fb_q, vid), (ok, reason) in zip(fb_downloads, fb_verdicts):
                            if ok:
                                final_path = output_dir / f"raw_{idx:02d}.jpg"
                                temp_path.replace(final_path)
                                chosen[idx] = (final_path, False, vid, False)
                            else:
                                logger.warning("REJECTED %s fallback photo %s for '%s': %s", stage_name, vid, fb_q, reason)
                                temp_path.unlink(missing_ok=True)

        # Beat 0 (Cover / Opening Hook) protection: promote first primary topic-matched clip to position 0
        ordered_items = [item for item in chosen if item is not None]
        if ordered_items and not ordered_items[0][3]:
            for pos, item in enumerate(ordered_items):
                if item[3]:
                    ordered_items.insert(0, ordered_items.pop(pos))
                    logger.info("Promoted primary topic-matched clip %s to Beat 0 (cover frame).", item[2])
                    break

        # Strict zero-duplicate enforcement: drop unfilled beats rather than ever reusing a clip
        downloaded: list[Path] = []
        newly_used_ids: list[str] = []
        seen_paths: set[Path] = set()
        seen_vids: set[str] = set()
        for path, _is_nasa, vid, _is_primary in ordered_items:
            if path in seen_paths or vid in seen_vids:
                continue
            seen_paths.add(path)
            seen_vids.add(vid)
            downloaded.append(path)
            newly_used_ids.append(vid)

        min_unique = min(len(working_queries), 5)
        if len(downloaded) < min_unique:
            raise RuntimeError(
                f"Only {len(downloaded)} unique safe visuals found (minimum {min_unique} required to prevent repetition)"
            )

        assert len(downloaded) == len(set(downloaded))
        assert len(newly_used_ids) == len(set(newly_used_ids))
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
            wm = [(v, u, False) for v, u in await self._search_wikimedia_photos(client, query) if v not in used_ids]
            wp = [(v, u, False) for v, u in await self._search_wikipedia_images(client, query) if v not in used_ids]
            ov = [(v, u, False) for v, u in await self._search_openverse_photos(client, query) if v not in used_ids]
            interleaved: list[tuple[str, str, bool]] = []
            max_len = max(len(wm), len(wp), len(ov), 0)
            for k in range(max_len):
                if k < len(wm):
                    interleaved.append(wm[k])
                if k < len(wp):
                    interleaved.append(wp[k])
                if k < len(ov):
                    interleaved.append(ov[k])
            return interleaved

        non_nasa: list[tuple[str, str, bool]] = []
        nasa_list: list[tuple[str, str, bool]] = []

        if not self._pexels_disabled and self.pexels_api_key:
            for orientation in ("portrait", None):
                if self._pexels_disabled:
                    break
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
            for vid, url in await self._search_wikipedia_images(client, query):
                if vid not in used_ids:
                    non_nasa.append((vid, url, False))
            for vid, url in await self._search_openverse_photos(client, query):
                if vid not in used_ids:
                    non_nasa.append((vid, url, False))

        if allow_nasa:
            for vid, url in await self._search_nasa(client, query):
                if vid not in used_ids:
                    nasa_list.append((vid, url, True))

        if prefer_nasa:
            return nasa_list + non_nasa
        return non_nasa + nasa_list
