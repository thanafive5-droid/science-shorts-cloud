"""Gemini-powered viral science Shorts micro-story script and visual beat generator."""

import difflib
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


VALID_BGM_MOODS: tuple[str, ...] = (
    "cosmic_ambient",
    "cinematic_tension",
    "upbeat_tech",
    "nature_organic",
    "energetic_drive",
    "mystery_piano",
)

CHANNEL_DEFAULT_MOODS: dict[str, str] = {
    "astronomy": "cosmic_ambient",
    "biology": "nature_organic",
    "chemistry": "energetic_drive",
    "earth_science": "cinematic_tension",
    "physics": "upbeat_tech",
    "general_science": "mystery_piano",
}

GENERIC_TITLE_STOPWORDS: frozenset[str] = frozenset({
    "the", "this", "that", "why", "how", "what", "when", "where", "into", "from",
    "with", "your", "can", "actually", "really", "just", "instantly", "turns",
    "makes", "does", "not", "never", "always", "most", "ever", "found", "real",
    "hidden", "hiding", "earth", "earths", "science", "liquid", "solid", "metal",
    "star", "planet", "fish", "animal", "bird", "ocean", "deep", "sea", "water",
    "world", "inside", "through", "without", "impossible", "secret", "dangerous",
    "massive", "giant", "tiny", "strange", "weird", "crazy", "alive", "survives",
})

PHENOMENON_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mpemba_effect", ("mpemba", "freezes faster", "hot water freeze", "freeze faster")),
    ("immortal_jellyfish", ("turritopsis", "immortal jellyfish", "resets its own life", "never grows old", "reverses age")),
    ("prince_rupert_drop", ("prince rupert", "glass teardrop", "unbreakable glass")),
    ("gallium_metal", ("gallium", "melts in your palm", "eats through solid aluminum")),
    ("superfluid_helium", ("superfluid", "climbs out of its container", "vanished into thin air")),
    ("mantis_pistol_shrimp", ("mantis shrimp", "pistol shrimp", "harder than a bullet", "louder than a jet")),
    ("bombardier_beetle", ("bombardier", "boiling chemical spray", "fires boiling liquid")),
    ("leidenfrost_effect", ("leidenfrost", "glide across fire", "slides on metal", "molten lead", "ice glide")),
    ("ferrofluid", ("ferrofluid", "grows its own spikes", "turns into spikes")),
    ("wood_frog", ("wood frog", "freezes solid", "freezes completely solid")),
    ("singing_sand_dunes", ("singing sand", "sand dunes", "musical desert")),
    ("hydrophobic_sand", ("hydrophobic sand", "refuses to get wet", "hates this leaf")),
)


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _distinctive_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in _normalize_text(text).split()
        if len(tok) >= 4 and tok not in GENERIC_TITLE_STOPWORDS
    }


def _matching_fingerprints(text: str) -> set[str]:
    norm = _normalize_text(text)
    matched: set[str] = set()
    for name, cues in PHENOMENON_FINGERPRINTS:
        if any(cue in norm for cue in cues):
            matched.add(name)
    return matched


def _find_duplicate_match(
    candidate_topic: str, candidate_title: str, existing_items: list[str]
) -> str | None:
    """Return colliding existing title/topic if candidate duplicates or closely rehashes an earlier Short."""
    norm_title = _normalize_text(candidate_title)
    norm_topic = _normalize_text(candidate_topic)
    cand_combo = f"{candidate_title} {candidate_topic}"
    cand_tokens = _distinctive_tokens(cand_combo)
    cand_fps = _matching_fingerprints(cand_combo)

    for old_item in existing_items:
        norm_old = _normalize_text(old_item)
        if not norm_old:
            continue
        if norm_old == norm_title or norm_old == norm_topic:
            return old_item
        if (
            difflib.SequenceMatcher(None, norm_title, norm_old).ratio() >= 0.72
            or difflib.SequenceMatcher(None, norm_topic, norm_old).ratio() >= 0.72
        ):
            return old_item

        old_fps = _matching_fingerprints(old_item)
        if cand_fps and old_fps and (cand_fps & old_fps):
            return old_item

        old_tokens = _distinctive_tokens(old_item)
        if cand_tokens and old_tokens:
            overlap = len(cand_tokens & old_tokens)
            union = len(cand_tokens | old_tokens)
            jaccard = overlap / union if union else 0.0
            if overlap >= 3 or (overlap >= 2 and jaccard >= 0.40):
                return old_item
    return None


@dataclass
class ShortScript:
    """Complete structured script for a 9:16 Science Short."""

    topic: str
    title: str
    description: str
    tags: list[str]
    beats: list[VisualBeat]
    topic_fallback_queries: list[str] = field(default_factory=list)
    bgm_mood: str = "upbeat_tech"
    comment_prompt: str = ""

    @property
    def full_narration(self) -> str:
        return " ".join(beat.narration.strip() for beat in self.beats if beat.narration.strip())


NARRATIVE_ARCS: tuple[tuple[str, str], ...] = (
    (
        "Micro-Story: Setup -> Escalation -> Payoff",
        "Tell a vivid 20-second micro-story centered on ONE real physical object, creature, or geological wonder: open inside the action, escalate the danger or paradox around Beat 6, and reveal the true mechanism at the climax.",
    ),
    (
        "Micro-Story: Myth-Bust Investigation",
        "Open by shattering a popular myth about a real visible object/creature in Beat 1, prove what really happens in Beats 2–8, and close with the unforgettable scientific truth.",
    ),
    (
        "Micro-Story: Extreme Survival Challenge",
        "Drop the viewer straight into an extreme real-world environment in Beat 1 (crush depth, acid lake, lava, cosmic void) and follow one real protagonist or material second by second to a surprising ending.",
    ),
    (
        "Micro-Story: Hidden Anomaly Discovery",
        "Open with a bizarre real discovery scientists struggled to explain, escalate the mystery mid-story around Beat 6, and deliver the jaw-dropping cause in the final 2 beats.",
    ),
    (
        "Micro-Story: Human-Scale Showdown",
        "Anchor one astonishing physical object or creature to an instant everyday human comparison (louder than a jet, hotter than lava, stronger than Kevlar) and trace its micro-story to an open-loop closer.",
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
            if len(merged_avoid) >= 150:
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
                '2. **Scroll-Stopping Hook (Beat 1)**: MUST begin literally with **"Wait,"** '
                'followed immediately by a shocking curiosity gap '
                '(e.g., "Wait, this fish survives crushing abyssal pressure.", '
                '"Wait, this metal explodes in water.") '
                "to freeze the viewer's thumb in the first 1.5 seconds. NEVER use em-dashes (—)."
            )
        else:
            hook_rule = (
                '2. **Scroll-Stopping Hook (Beat 1)**: Open with an irresistible, counter-intuitive claim '
                'or urgent mystery in 4 to 7 words '
                '(e.g., "This frog freezes solid and lives.", '
                '"This snail wears real iron armor."). '
                'NEVER sound like a textbook, NEVER use em-dashes (—), and NEVER start with "Wait", "Did you know", or "Hello guys".'
            )

        arc_name, arc_guidance = random.SystemRandom().choice(NARRATIVE_ARCS)
        pool_tags = [h for h in channel.default_hashtags if h.lower() != "#shorts"]
        sample_count = min(2, len(pool_tags))
        chosen_tags = random.SystemRandom().sample(pool_tags, sample_count) if sample_count else []
        desc_tags = " ".join([*chosen_tags, "#shorts"])

        system_instruction = (
            "You are an elite YouTube Shorts producer specializing in ultra-tight, high-retention "
            "English-language science videos (~18 to 22 seconds long, 48 to 62 words TOTAL). "
            "Your scripts use rapid 1.0–2.5 second visual hard cuts (11 to 13 beats), "
            "smooth continuous spoken sentences across beats so text-to-speech sounds completely natural, "
            "and concrete real-world search queries that always return relevant photography."
        )

        base_prompt = f"""
Create ONE brand-new YouTube Short script for the **{channel.display_name}** channel.
Channel niche: {channel.niche_description}
Chosen Narrative Arc for THIS video: **{arc_name}** — {arc_guidance}

TOPICS & PUBLISHED TITLES ALREADY USED (DO NOT REPEAT OR RE-ANGLE THESE):
{avoid_block}

{adaptation_block}STRICT REQUIREMENTS:
1. **Ultra-Tight Length (~18–22 seconds total)**: STRICTLY **48 to 62 words TOTAL** across all beats (~4–5 words per beat). NEVER exceed 66 words and NEVER write fewer than 44 words. Zero fluff, zero filler, zero "like and subscribe" begging.
{hook_rule}
3. **Mid-Video Retention Reset & Engagement Closer**: Around Beat 6 (~second 9), inject a tiny sharp pivot ("But here is the catch,", "Until pressure hits,", "Then it flips,") to reset viewer attention. End Beat 11–13 with a crisp 4-to-5-word payoff or question. NEVER say "Don't forget to like and subscribe", "follow for more", or "thanks for watching".
4. **Rapid-Cut 1.0–2.5s Visuals (STRICTLY 11 to 13 beats) WITH SMOOTH CONTINUOUS SPEECH**: Split the narration across **11 to 13 visual beats** (~4–5 words per beat so visuals cut every 1.0 to 2.5 seconds!):
   - **SMOOTH CONTINUOUS SPEECH RULE (CRITICAL FOR VOICE QUALITY)**: Write the full narration as **4 to 6 smooth, naturally connected conversational sentences** flowing seamlessly across the 11–13 beats. Do **NOT** put a period (`.`) at the end of every 4-word beat! Let each natural sentence span 2 to 3 consecutive visual beats (with normal commas only where a human pauses), and **NEVER use em-dashes (`—`) or robotic ellipses (`...`)**.
   - **ANTI-TEMPLATE / NATURAL HUMAN VOICE**: Write like a sharp human science creator talking smoothly to a friend (Veritasium / NileRed tone): clear everyday spoken English, zero melodrama. STRICTLY BAN AI clichés: "mind-bending", "mind-blowing", "astonishing", "incredible", "jaw-dropping", "defies all logic", "defies reality", "believe it or not", "buckle up", "delve", "tapestry", "testament", "unravel", "imagine a world", "here's the crazy part", "secretly".
   - **STRICT SAFETY**: 100% G-rated, family-friendly, YouTube-safe (NO nudity, NO graphic medical/surgery/blood, NO violence, NO people/crowds/sports).
   - **CONCRETE SEARCHABLE QUERIES (2–4 words max per beat)**: Write simple, concrete real-world visual queries that Wikimedia Commons / Wikipedia actually index as real photography/video—and ALWAYS include the subject/habitat anchor so every clip matches the SAME world!
     - GOOD (deep-sea fish example): `"deep sea snailfish"`, `"mariana trench ocean"`, `"anglerfish deep sea"`, `"underwater abyssal fish"`, `"jellyfish deep ocean"`, `"submarine deep sea"`, `"hydrothermal vent ocean"`, `"bioluminescent marine life"`, `"coral reef fish"`, `"deep water ocean"`, `"seafloor sediment ocean"`
     - BAD: `"dark endless deep ocean abyss"` (too poetic), `"deep sea creature skeleton x-ray style"` (returns odd unrelated hits), or `"parrot fish"` without habitat (write `"parrotfish coral reef"`).
     - NEVER use abstract words, title screens, slides, charts, diagrams, or people in `search_query`.
   - **CRITICAL `topic_fallback_queries`**: Provide **5 to 6 broader real-camera backup queries STRICTLY within the SAME subject and habitat** (e.g., for deep-sea fish: `["deep sea fish", "anglerfish deep sea", "underwater ocean coral", "bioluminescent jellyfish", "submarine ocean trench", "marine fish underwater"]`). NEVER suggest off-habitat queries (never forest, birds, desert, volcano, etc. for an ocean video!).
5. **Scientific Accuracy**: 100% real peer-reviewed facts, numbers, and units. Zero myths or hallucinations.
6. **Metadata, Micro-Story Closer & Engagement Comment (`comment_prompt`)**:
   - `title`: Crisp natural Title Case YouTube Shorts title under 62 chars — **NO `#shorts` or hashtags in title**. NEVER end with "Explained", NEVER start with "The Physics of..." or "It...". MUST be 100% distinct from every title in the avoid list above!
   - `description`: 1–2 concise natural sentences + hashtags: {desc_tags}
   - `tags`: 6-10 relevant SEO keywords
   - `comment_prompt`: One friendly, curious starter comment (under 110 chars, zero spam/links/subscribe begging) asking viewers a fun question or what extreme phenomenon they want covered next (e.g., `"Would you dare swim near this hydrothermal vent? Drop the next ocean mystery below!"`).
   - `bgm_mood`: Choose the SINGLE best musical genre for THIS specific video's topic & emotion from:
     - `"cosmic_ambient"`: space, stars, black holes, deep-sea abyss, serene cosmic awe
     - `"cinematic_tension"`: dangerous phenomena, volcanoes, predators, explosions, survival stakes
     - `"upbeat_tech"`: physics paradoxes, clever materials, optical/fluid illusions, engineering
     - `"nature_organic"`: wildlife, plants, coral reefs, ecosystems, natural earth beauty
     - `"energetic_drive"`: explosive chemistry, lightning, extreme speed or energy records
     - `"mystery_piano"`: bizarre puzzles, ancient secrets, myth-busting, weird counterintuitive facts

Return ONLY valid JSON matching this exact schema:
{{
  "topic": "short unique topic name",
  "title": "Catchy Human Title Without Hashtags",
  "description": "Short description with hashtags",
  "tags": ["science", "nature"],
  "bgm_mood": "cinematic_tension",
  "comment_prompt": "Short friendly curiosity question for viewers",
  "topic_fallback_queries": ["broad same-habitat query 1", "broad same-habitat query 2", "..."],
  "beats": [
    {{"narration": "Wait, this deep-sea fish", "search_query": "deep sea snailfish"}},
    {{"narration": "survives crushing ocean pressure.", "search_query": "mariana trench ocean"}},
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
        for attempt in range(3):
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
                beats[0].narration = f"Wait, {beats[0].narration}"

            clean_title = re.sub(r"\s*#shorts?\b", "", str(raw["title"]), flags=re.IGNORECASE).strip()[:95]
            raw_fb = raw.get("topic_fallback_queries") or []
            topic_fbs = [str(q).strip() for q in raw_fb if str(q).strip()]
            # Guarantee non-empty same-topic backup pool
            for b in beats:
                if b.search_query and b.search_query not in topic_fbs:
                    topic_fbs.append(b.search_query)

            raw_mood = str(raw.get("bgm_mood") or "").strip().lower()
            bgm_mood = raw_mood if raw_mood in VALID_BGM_MOODS else CHANNEL_DEFAULT_MOODS.get(channel.id, "upbeat_tech")

            script = ShortScript(
                topic=str(raw["topic"]).strip(),
                title=clean_title,
                description=str(raw.get("description", "")).strip(),
                tags=[str(t).strip() for t in raw.get("tags", [])][:12],
                beats=beats,
                topic_fallback_queries=topic_fbs[:12],
                bgm_mood=bgm_mood,
                comment_prompt=str(raw.get("comment_prompt") or "").strip()[:200],
            )
            last_script = script
            word_count = len(script.full_narration.split())

            dup_match = _find_duplicate_match(script.topic, script.title, merged_avoid)
            if dup_match:
                logger.warning(
                    "REJECTED DUPLICATE script '%s' (%s) — too close to '%s'; regenerating (attempt %d/3)...",
                    script.title,
                    script.topic,
                    dup_match,
                    attempt + 1,
                )
                if attempt < 2:
                    prompt = (
                        base_prompt
                        + f"\nREJECTED DUPLICATE: Your draft '{script.title}' (topic: '{script.topic}') is too similar to "
                        f"previously published Short '{dup_match}'. Pick a 100% DIFFERENT scientific phenomenon and completely fresh title!\n"
                    )
                    continue

            if not (38 <= word_count <= 68 and len(script.beats) >= 10):
                if attempt < 2:
                    logger.warning(
                        "Script pacing out of bounds (%d beats, %d words); regenerating for strict 1.0-2.5s pacing...",
                        len(script.beats),
                        word_count,
                    )
                    prompt = (
                        base_prompt
                        + f"\nCORRECTION REQUIRED: Your previous draft had {len(script.beats)} beats and {word_count} words. "
                        "You MUST output 11 to 13 beats and STRICTLY 48 to 62 words TOTAL (~4-5 words per beat).\n"
                    )
                    continue

            break

        assert last_script is not None
        logger.info(
            "Generated script for [%s]: '%s' (%d beats, %d words, mood=%s)",
            channel.id,
            last_script.title,
            len(last_script.beats),
            len(last_script.full_narration.split()),
            last_script.bgm_mood,
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
