"""Gemini-powered viral science Shorts script and visual beat generator."""

import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from src.analytics_adapter import build_adaptation_context
from src.config import ChannelConfig

logger = logging.getLogger(__name__)


@dataclass
class VisualBeat:
    """A short 1.0-2.5 second visual beat paired with a slice of narration."""

    narration: str
    search_query: str


@dataclass
class ShortScript:
    """Complete structured script for a 9:16 Science Short."""

    topic: str
    title: str
    description: str
    tags: list[str]
    beats: list[VisualBeat]
    topic_fallback_queries: list[str] = field(default_factory=list)

    @property
    def full_narration(self) -> str:
        return " ".join(beat.narration.strip() for beat in self.beats if beat.narration.strip())


NARRATIVE_ARCS: tuple[tuple[str, str], ...] = (
    (
        "Myth-Bust",
        "Shatter a widespread misconception in Beat 1, escalate with real scientific proof in the middle beats, and deliver the surprising real mechanism at the end.",
    ),
    (
        "Extreme Human-Scale Comparison",
        "Anchor the phenomenon to a vivid, jaw-dropping everyday human-scale comparison right away (e.g., louder than a jet engine, hotter than the Sun's surface, heavier than a skyscraper).",
    ),
    (
        "Mystery-to-Reveal (Open Loop)",
        "Open an intriguing visual mystery in Beat 1 without giving away the cause yet, raise the stakes around Beat 6, and withhold the shocking payoff until the final 2 beats.",
    ),
    (
        "High-Stakes 'What If / Survival'",
        "Drop the viewer straight inside the extreme condition in Beat 1 (e.g., 'If you dropped this in water...', 'If you stood here for one second...') and trace the real physics/biology second by second.",
    ),
    (
        "Seamless Curiosity Loop",
        "Deliver a complete, satisfying scientific reveal, and craft the final 4–5 words so their cadence naturally tempts viewers to rewatch or comment while standing cleanly on their own.",
    ),
)


class ScriptGenerator:
    """Generates high-retention English science Shorts scripts via Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-lite") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def generate(self, channel: ChannelConfig, recent_topics: list[str]) -> ShortScript:
        """Generate a tight, fast-paced script (48-62 words, 11-13 beats for 1.0-2.5s cuts)."""
        adaptation_block, published_titles = build_adaptation_context(channel.id, channel.youtube_handle)

        merged_avoid: list[str] = []
        seen_norm: set[str] = set()
        for raw_topic in [*recent_topics, *published_titles]:
            clean = str(raw_topic).strip()
            norm = " ".join(clean.lower().split())
            if clean and norm not in seen_norm:
                seen_norm.add(norm)
                merged_avoid.append(clean)
            if len(merged_avoid) >= 90:
                break
        avoid_block = "\n".join(f"- {t}" for t in merged_avoid) if merged_avoid else "None yet."

        # Spread "Wait..." hooks naturally across different times of day & channels:
        now_utc = datetime.now(timezone.utc)
        slot_index = now_utc.hour // 4
        channel_offset = {
            "physics": 0,
            "chemistry": 1,
            "biology": 2,
            "general_science": 3,
            "earth_science": 4,
            "astronomy": 5,
        }.get(channel.id, 0)
        base_slot = (now_utc.timetuple().tm_yday + channel_offset) % 6
        if now_utc.weekday() in (0, 2, 4):
            use_wait_hook = slot_index in (base_slot, (base_slot + 3) % 6)
        else:
            use_wait_hook = slot_index == base_slot

        if use_wait_hook:
            hook_rule = (
                '2. **Scroll-Stopping Hook (Beat 1)**: MUST begin literally with **"Wait,"** or **"Wait—"** '
                'followed immediately by a shocking curiosity gap '
                '(e.g., "Wait—this fish survives crushing abyssal pressure.", '
                '"Wait, this metal explodes in water.") '
                "to freeze the viewer's thumb in the first 1.5 seconds."
            )
        else:
            hook_rule = (
                '2. **Scroll-Stopping Hook (Beat 1)**: Open with an irresistible, counter-intuitive claim '
                'or urgent mystery in 4 to 7 words '
                '(e.g., "This frog freezes solid and lives.", '
                '"This snail wears real iron armor."). '
                'NEVER sound like a textbook, and NEVER start with "Wait", "Did you know", or "Hello guys".'
            )

        arc_name, arc_guidance = random.SystemRandom().choice(NARRATIVE_ARCS)
        pool_tags = [h for h in channel.default_hashtags if h.lower() != "#shorts"]
        sample_count = min(2, len(pool_tags))
        chosen_tags = random.SystemRandom().sample(pool_tags, sample_count) if sample_count else []
        desc_tags = " ".join([*chosen_tags, "#shorts"])

        system_instruction = (
            "You are an elite YouTube Shorts producer specializing in ultra-tight, high-retention "
            "English-language science videos (~17 to 21 seconds long, 48 to 62 words TOTAL). "
            "Your scripts use rapid 1.0–2.5 second visual hard cuts (11 to 13 beats), "
            "crisp everyday spoken pacing for short attention spans, "
            "and concrete real-world search queries that always return relevant photography."
        )

        base_prompt = f"""
Create ONE brand-new YouTube Short script for the **{channel.display_name}** channel.
Channel niche: {channel.niche_description}
Chosen Narrative Arc for THIS video: **{arc_name}** — {arc_guidance}

TOPICS & PUBLISHED TITLES ALREADY USED (DO NOT REPEAT OR RE-ANGLE THESE):
{avoid_block}

{adaptation_block}STRICT REQUIREMENTS:
1. **Ultra-Tight Length (~17–21 seconds total)**: STRICTLY **48 to 62 words TOTAL** across all beats (~4–5 words per beat). NEVER exceed 66 words and NEVER write fewer than 44 words. Zero fluff, zero filler, zero "like and subscribe" begging.
{hook_rule}
3. **Mid-Video Retention Reset & Engagement Closer**: Around Beat 6 (~second 9), inject a tiny sharp pivot ("But here is the catch...", "Until pressure hits...", "Then it flips...") to reset viewer attention. End Beat 11–13 with a crisp 4-to-5-word payoff or question. NEVER say "Don't forget to like and subscribe", "follow for more", or "thanks for watching".
4. **Rapid-Cut 1.0–2.5s Visuals (STRICTLY 11 to 13 beats)**: Split the narration into **11 to 13 punchy beats** (each beat ~4–5 spoken words so visuals cut every 1.0 to 2.5 seconds!).
   - **ANTI-TEMPLATE / NATURAL HUMAN VOICE**: Write like a sharp human science creator talking fast to a friend (Veritasium / NileRed tone): short punchy phrases, zero melodrama. STRICTLY BAN AI clichés: "mind-bending", "mind-blowing", "astonishing", "incredible", "jaw-dropping", "defies all logic", "defies reality", "believe it or not", "buckle up", "delve", "tapestry", "testament", "unravel", "imagine a world", "here's the crazy part", "secretly".
   - **STRICT SAFETY**: 100% G-rated, family-friendly, YouTube-safe (NO nudity, NO graphic medical/surgery/blood, NO violence, NO people/crowds/sports).
   - **CONCRETE SEARCHABLE QUERIES (2–4 words max per beat)**: Write simple, concrete real-world visual queries that Wikimedia Commons / Wikipedia actually index as real photography/video—and ALWAYS include the subject/habitat anchor so every clip matches the SAME world!
     - GOOD (deep-sea fish example): `"deep sea snailfish"`, `"mariana trench ocean"`, `"anglerfish deep sea"`, `"underwater abyssal fish"`, `"jellyfish deep ocean"`, `"submarine deep sea"`, `"hydrothermal vent ocean"`, `"bioluminescent marine life"`, `"coral reef fish"`, `"deep water ocean"`, `"seafloor sediment ocean"`
     - BAD: `"dark endless deep ocean abyss"` (too poetic), `"deep sea creature skeleton x-ray style"` (returns odd unrelated hits), or `"parrot fish"` without habitat (write `"parrotfish coral reef"`).
     - NEVER use abstract words, title screens, slides, charts, diagrams, or people in `search_query`.
   - **CRITICAL `topic_fallback_queries`**: Provide **5 to 6 broader real-camera backup queries STRICTLY within the SAME subject and habitat** (e.g., for deep-sea fish: `["deep sea fish", "anglerfish deep sea", "underwater ocean coral", "bioluminescent jellyfish", "submarine ocean trench", "marine fish underwater"]`). NEVER suggest off-habitat queries (never forest, birds, desert, volcano, etc. for an ocean video!).
5. **Scientific Accuracy**: 100% real peer-reviewed facts, numbers, and units. Zero myths or hallucinations.
6. **Metadata**:
   - `title`: Crisp natural Title Case YouTube Shorts title under 62 chars — **NO `#shorts` or hashtags in title**. NEVER end with "Explained", NEVER start with "The Physics of..." or "It...".
   - `description`: 1–2 concise natural sentences + hashtags: {desc_tags}
   - `tags`: 6-10 relevant SEO keywords

Return ONLY valid JSON matching this exact schema:
{{
  "topic": "short unique topic name",
  "title": "Catchy Human Title Without Hashtags",
  "description": "Short description with hashtags",
  "tags": ["science", "nature"],
  "topic_fallback_queries": ["broad same-habitat query 1", "broad same-habitat query 2", "..."],
  "beats": [
    {{"narration": "4-5 word hook phrase.", "search_query": "2-4 word concrete photo query"}},
    ...
  ]
}}
"""

        candidate_models = list(
            dict.fromkeys([
                self.model,
                "gemini-3.1-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.7-flash",
            ])
        )

        last_script: ShortScript | None = None
        prompt = base_prompt
        for attempt in range(2):
            response = None
            last_err: Exception | None = None
            for model_name in candidate_models:
                try:
                    response = self._call_gemini_with_retry(model_name, system_instruction, prompt)
                    if model_name != self.model:
                        logger.warning("Used fallback Gemini model: %s", model_name)
                    break
                except Exception as exc:
                    logger.warning("Model %s failed (%s), trying next fallback...", model_name, exc)
                    last_err = exc

            if response is None:
                raise RuntimeError(f"All Gemini models failed: {last_err}") from last_err

            raw = json.loads(response.text)
            beats = [
                VisualBeat(
                    narration=str(b.get("narration") or b.get("text") or "").strip(),
                    search_query=str(
                        b.get("search_query")
                        or b.get("query")
                        or b.get("visual_query")
                        or b.get("visual")
                        or f"{channel.display_name.lower()} science"
                    ).strip(),
                )
                for b in raw.get("beats", [])
                if isinstance(b, dict) and str(b.get("narration") or b.get("text") or "").strip()
            ]

            if len(beats) < 5:
                raise ValueError(f"Generated script had too few beats ({len(beats)})")

            if use_wait_hook and not beats[0].narration.lower().startswith("wait"):
                beats[0].narration = f"Wait— {beats[0].narration}"

            clean_title = re.sub(r"\s*#shorts?\b", "", str(raw["title"]), flags=re.IGNORECASE).strip()[:95]
            raw_fb = raw.get("topic_fallback_queries") or []
            topic_fbs = [str(q).strip() for q in raw_fb if str(q).strip()]
            # Guarantee non-empty same-topic backup pool
            for b in beats:
                if b.search_query and b.search_query not in topic_fbs:
                    topic_fbs.append(b.search_query)

            script = ShortScript(
                topic=str(raw["topic"]).strip(),
                title=clean_title,
                description=str(raw.get("description", "")).strip(),
                tags=[str(t).strip() for t in raw.get("tags", [])][:12],
                beats=beats,
                topic_fallback_queries=topic_fbs[:12],
            )
            last_script = script
            word_count = len(script.full_narration.split())

            if 38 <= word_count <= 68 and len(script.beats) >= 10:
                break

            if attempt == 0:
                logger.warning(
                    "Script pacing out of bounds (%d beats, %d words); regenerating once for strict 1.0-2.5s pacing...",
                    len(script.beats),
                    word_count,
                )
                prompt = (
                    base_prompt
                    + f"\nCORRECTION REQUIRED: Your previous draft had {len(script.beats)} beats and {word_count} words. "
                    "You MUST output 11 to 13 beats and STRICTLY 48 to 62 words TOTAL (~4-5 words per beat).\n"
                )

        assert last_script is not None
        logger.info(
            "Generated script for [%s]: '%s' (%d beats, %d words)",
            channel.id,
            last_script.title,
            len(last_script.beats),
            len(last_script.full_narration.split()),
        )
        return last_script

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=10))
    def _call_gemini_with_retry(
        self, model_name: str, system_instruction: str, prompt: str
    ):
        return self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.9,
                http_options=types.HttpOptions(timeout=45000),
            ),
        )
