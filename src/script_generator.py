"""Gemini-powered viral science Shorts script and visual beat generator."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

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
        avoid_block = "\n".join(f"- {t}" for t in recent_topics[:60]) if recent_topics else "None yet."

        # 2 out of the 6 daily 4-hour slots (04:00-07:59 UTC & 16:00-19:59 UTC) use a "Wait..." hook
        slot_index = datetime.now(timezone.utc).hour // 4
        use_wait_hook = slot_index in (1, 4)

        if use_wait_hook:
            hook_rule = (
                '2. **Hook (Beat 1)**: MUST begin literally with the word **"Wait,"** or **"Wait—"** '
                '(e.g., "Wait—why does...", "Wait, have you ever wondered why...", "Wait, this actually happens when...") '
                "to create an instant pattern-interrupt in the first 2 seconds."
            )
        else:
            hook_rule = (
                '2. **Hook (Beat 1)**: Grab attention in the first 2 seconds with a shocking fact, paradox, '
                'or bold question. Do NOT start with "Wait", "Did you know", or "Hello guys".'
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

TOPICS ALREADY USED (DO NOT REPEAT OR CLOSELY PARAPHRASE THESE):
{avoid_block}

STRICT REQUIREMENTS:
1. **Length (~21 seconds total)**: The science explanation MUST be **48 to 56 words** (~20 seconds), followed by a quick 1-second closing line: **"Don't forget to like and subscribe!"** (so total script is ~54 to 62 words = ~21 seconds).
{hook_rule}
3. **Complete Final Sentence + CTA**: Finish the full science explanation cleanly, then end the final beat with **"Don't forget to like and subscribe!"**.
4. **Fast-Paced Relevant & 100% Family-Safe Real-Video Visuals (7 to 9 beats)**: Split the narration into **7 to 9 beats** (~6-8 words each = ~2.2-2.8 seconds per cut).
   - **STRICT SAFETY**: Every topic and `search_query` MUST be 100% G-rated, family-friendly, and YouTube-safe (NO nudity, NO graphic anatomy/surgery/blood, NO violence, NO people/crowds/sports).
   - **CRITICAL FOR BEAT 1 (OPENING HOOK)**: `search_query` MUST be vivid, dramatic real-world nature/space/science/lab footage (e.g. "volcano lava eruption", "lightning storm earth", "ocean coral reef", "sun solar flare", "aurora borealis space", "water droplet microgravity"). NEVER choose queries that return title screens, slides, text graphics, diagrams, or people talking.
   - Every beat's `search_query` (2-3 concrete English nouns/verbs) MUST directly match the physical object or phenomenon being spoken about in that exact beat.
5. **Metadata**:
   - `title`: Catchy YouTube Shorts title under 75 chars, ending with #shorts
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
            dict.fromkeys([self.model, "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"])
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
                narration=str(b["narration"]).strip(),
                search_query=str(b["search_query"]).strip(),
            )
            for b in raw.get("beats", [])
            if str(b.get("narration", "")).strip()
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

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=2, min=2, max=6))
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
                http_options=types.HttpOptions(timeout=25000),
            ),
        )
