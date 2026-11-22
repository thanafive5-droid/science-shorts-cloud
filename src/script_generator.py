"""Gemini-powered viral science Shorts script and visual beat generator."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from src.analytics_adapter import build_adaptation_context
from src.config import ChannelConfig

logger = logging.getLogger(__name__)


@dataclass
class VisualBeat:
    """A short 2-3 second visual beat paired with a slice of narration."""

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

    @property
    def full_narration(self) -> str:
        text = " ".join(beat.narration.strip() for beat in self.beats if beat.narration.strip())
        cta = "Don't forget to like and subscribe!"
        if "like and subscribe" not in text.lower():
            text = f"{text} {cta}"
        return text


class ScriptGenerator:
    """Generates high-retention English science Shorts scripts via Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def generate(self, channel: ChannelConfig, recent_topics: list[str]) -> ShortScript:
        """Generate a unique, complete, fast-paced script for the given channel with model fallback."""
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
        # 3 days/week (Mon/Wed/Fri): 2 of 6 daily clips (spaced 12h apart)
        # Other 4 days/week (Tue/Thu/Sat/Sun): 1 of 6 daily clips
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
                'followed immediately by a shocking, mind-bending curiosity gap '
                '(e.g., "Wait—there is a metal that explodes the second it touches water.", '
                '"Wait, your body is literally glowing right now—here is why you cannot see it.") '
                "to freeze the viewer's thumb in the first 2 seconds."
            )
        else:
            hook_rule = (
                '2. **Scroll-Stopping Hook (Beat 1)**: Open with an irresistible, counter-intuitive claim, '
                'impossible-sounding fact, or urgent mystery in under 8 words '
                '(e.g., "This frog freezes completely solid—and wakes up alive.", '
                '"One teaspoon of this star weighs a billion tons."). '
                'NEVER sound like a textbook, and NEVER start with "Wait", "Did you know", or "Hello guys".'
            )

        system_instruction = (
            "You are an elite YouTube Shorts producer specializing in viral, scientifically accurate "
            "English-language science videos (~20 seconds long). "
            "Your scripts have an irresistible 2-second hook, fast-paced 2-3 second visual cuts, "
            "and ALWAYS deliver a complete, crystal-clear explanation of the main scientific idea "
            "ending in a complete, punchy final sentence."
        )

        prompt = f"""
Create ONE brand-new YouTube Short script for the **{channel.display_name}** channel.
Channel niche: {channel.niche_description}

TOPICS & PUBLISHED TITLES ALREADY USED (DO NOT REPEAT OR RE-ANGLE THE SAME UNDERLYING PHENOMENON UNDER DIFFERENT WORDING, e.g., supercooled water, immortal jellyfish, quantum levitation, ferrofluid, Neptune diamond rain):
{avoid_block}

{adaptation_block}STRICT REQUIREMENTS:
1. **Length (~21 seconds total)**: The science explanation MUST be **48 to 56 words** (~20 seconds), followed by a quick 1-second closing line: **"Don't forget to like and subscribe!"** (so total script is ~54 to 62 words = ~21 seconds).
{hook_rule}
3. **Complete Final Sentence + CTA**: Finish the full science explanation cleanly, then end the final beat with **"Don't forget to like and subscribe!"**.
4. **Fast-Paced Relevant & 100% Family-Safe Real-Video Visuals (7 to 9 beats)**: Split the narration into **7 to 9 beats** (~6-8 words each = ~2.2-2.8 seconds per cut).
   - **STRICT SAFETY**: Every topic and `search_query` MUST be 100% G-rated, family-friendly, and YouTube-safe (NO nudity, NO graphic anatomy/surgery/blood, NO violence, NO people/crowds/sports).
   - **CRITICAL FOR BEAT 1 (OPENING HOOK)**: `search_query` MUST be vivid, dramatic real-world nature/space/science/lab footage (e.g. "volcano lava eruption", "lightning storm earth", "ocean coral reef", "sun solar flare", "aurora borealis space", "water droplet microgravity"). NEVER choose queries that return title screens, slides, text graphics, diagrams, or people talking.
   - Every beat's `search_query` (2-3 concrete English nouns/verbs) MUST directly match the physical object or phenomenon being spoken about in that exact beat.
5. **Strict Scientific Fact-Check**: Double-check EVERY number, unit, cause-and-effect claim, and scientific term before outputting. Zero myths, zero exaggerations, zero hallucinations—100% peer-reviewed scientific accuracy!
6. **Metadata**:
   - `title`: Crisp Title Case YouTube Shorts title under 65 chars, ending with #shorts, built around a concrete curiosity gap or surprising physical contrast. NEVER end with "Explained", NEVER use dry textbook phrasing like "The Physics of..." or "Why Pure Water...", and NEVER start with flat "It...".
   - `description`: 2-sentence summary + relevant hashtags: {" ".join(channel.default_hashtags)}
   - `tags`: 6-10 relevant SEO keywords

Return ONLY valid JSON matching this exact schema:
{{
  "topic": "short unique topic name",
  "title": "Catchy title #shorts",
  "description": "Short description with hashtags",
  "tags": ["science", "physics", "shorts"],
  "beats": [
    {{"narration": "First punchy hook sentence.", "search_query": "concrete stock video query"}},
    ...
  ]
}}
"""

        candidate_models = list(
            dict.fromkeys([
                "gemini-3.1-flash-lite",
                self.model,
                "gemini-3.5-flash",
                "gemini-3.7-flash",
            ])
        )
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

        script = ShortScript(
            topic=str(raw["topic"]).strip(),
            title=str(raw["title"]).strip()[:95],
            description=str(raw.get("description", "")).strip(),
            tags=[str(t).strip() for t in raw.get("tags", [])][:12],
            beats=beats,
        )
        logger.info(
            "Generated script for [%s]: '%s' (%d beats, %d words)",
            channel.id,
            script.title,
            len(script.beats),
            len(script.full_narration.split()),
        )
        return script

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
