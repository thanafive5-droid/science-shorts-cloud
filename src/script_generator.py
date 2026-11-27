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
    "curious_pizzicato",
    "suspense_underscore",
    "sleek_documentary",
    "minimal_ambient",
    "quirky_plucked",
    "mystery_keys",
)

CHANNEL_DEFAULT_MOODS: dict[str, str] = {
    "astronomy": "minimal_ambient",
    "biology": "quirky_plucked",
    "chemistry": "curious_pizzicato",
    "earth_science": "suspense_underscore",
    "physics": "sleek_documentary",
    "general_science": "mystery_keys",
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
    ("mantis_pistol_shrimp", ("mantis shrimp", "pistol shrimp", "snapping shrimp", "harder than a bullet", "louder than a jet", "boiling bubbles", "cavitation bubble")),
    ("bombardier_beetle", ("bombardier", "boiling chemical spray", "fires boiling liquid")),
    ("leidenfrost_effect", ("leidenfrost", "glide across fire", "slides on metal", "molten lead", "ice glide")),
    ("ferrofluid", ("ferrofluid", "grows its own spikes", "turns into spikes")),
    ("wood_frog", ("wood frog", "freezes solid", "freezes completely solid")),
    ("singing_sand_dunes", ("singing sand", "sand dunes", "musical desert")),
    ("hydrophobic_sand", ("hydrophobic sand", "refuses to get wet", "hates this leaf")),
    ("mariana_snailfish", ("snailfish", "mariana trench", "impossible depths", "deep sea fish")),
    ("brinicle_icicle", ("brinicle", "death icicle", "icicle of antarctica")),
    ("cordyceps_fungus", ("cordyceps", "fungus controls insect", "zombie ant fungus")),
    ("boiling_river", ("boiling river", "river that literally boils", "shanay timpishka")),
    ("slinky_drop", ("slinky", "bottom of this slinky")),
    ("barrelfish_transparent", ("barreleye", "transparent head")),
    ("thermite_iron", ("thermite", "melts through iron", "rust powder aluminum")),
    ("molten_glass_planet", ("molten glass", "rains glass", "hd 189733b")),
    ("radioactive_mushrooms", ("chernobyl mushroom", "mushrooms feed on", "radiotrophic")),
    ("sea_slug_jellyfish", ("blue dragon", "glaucus", "sea slug eats deadly jellyfish")),
    ("soda_can_polymer", ("soda cans are not solid metal", "soda can plastic liner")),
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
            "You are an elite viral Science Shorts producer (inspired by Zack D. Films' crisp, addictive chronological storytelling). "
            "You write calm, deadpan-curious, crystal-clear English micro-stories (~22 to 27 seconds long, 56 to 72 words TOTAL) "
            "split across 12 to 14 rapid visual beats (~1.4 to 2.3 seconds per cut), paired with ultra-specific scientific search queries."
        )

        base_prompt = f"""
Create ONE brand-new YouTube Short script for the **{channel.display_name}** channel.
Channel niche: {channel.niche_description}
Chosen Narrative Arc for THIS video: **{arc_name}** — {arc_guidance}

TOPICS & PUBLISHED TITLES ALREADY USED (DO NOT REPEAT OR RE-ANGLE THESE):
{avoid_block}

{adaptation_block}STRICT REQUIREMENTS:
1. **Calm Zack-D Pacing (~22–27 seconds total)**: STRICTLY **56 to 72 words TOTAL** across all beats (~4–6 words per beat). NEVER exceed 76 words and NEVER write fewer than 48 words. Zero fluff, zero hype screaming, zero "like and subscribe" begging.
{hook_rule}
3. **Chronological Cause-and-Effect Micro-Story**:
   - Tell one complete chronological micro-story (**Setup → Escalation → Surprising Real Mechanism → Clean Payoff**) where every sentence advances the physical action (`"When a killer ant slips inside..."`, `"Within seconds..."`, `"Instead of dying..."`).
   - Around Beat 7 (~second 11), deliver a clear turning-point twist (`"But once it..."`, `"Instead, pressure forces..."`, `"That triggers..."`). End Beats 12–14 with an unforgettable scientific payoff—NOT "don't forget to subscribe" or vague filler.
4. **12 to 14 Rapid Visual Beats WITH SMOOTH CONTINUOUS SPEECH**:
   - **SMOOTH CONTINUOUS SPEECH RULE**: Write the entire script as **4 to 6 calm, smooth, naturally connected sentences** flowing across the **12 to 14 beats**. Do **NOT** put a period (`.`) at the end of every beat! Let each natural sentence flow across 2–3 visual cuts, and NEVER use em-dashes (`—`) or ellipses (`...`).
   - **DEADPAN-CURIOUS HUMAN TONE**: Sound like an intriguing, calm narrator explaining a crazy real mechanism plainly—zero forced excitement. STRICTLY BAN AI clichés: "mind-bending", "mind-blowing", "astonishing", "incredible", "jaw-dropping", "defies all logic", "defies reality", "believe it or not", "buckle up", "delve", "tapestry", "testament", "unravel", "imagine a world", "here's the crazy part", "secretly".
   - **SCIENTIFIC PHOTO SEARCH QUERIES (2–4 precise terms per beat)**: Every beat's `search_query` MUST use concrete **scientific species names, mineral/chemical names, apparatus, or unmistakable physical phenomena** so image APIs return sharp, unambiguous scientific photography—NEVER poetic fluff, vague metaphors, slides, diagrams, or people talking.
     - GOOD: `"Eciton army ant macro"`, `"Kangaroo joey pouch"`, `"Bismuth hopper crystal"`, `"Cherenkov blue radiation"`, `"Hydrothermal vent ocean"`, `"Volcano lava crater"`.
     - BAD: `"invisible force field"`, `"magical underwater world"`, `"dark endless void"`, `"amazing chemistry"`.
   - **CRITICAL `topic_fallback_queries`**: Provide **5 to 6 crisp scientific backup search queries** STRICTLY within the exact same organism/material/habitat (zero off-topic habitats!).
5. **Scientific Accuracy**: 100% real peer-reviewed facts, numbers, and units. Zero myths or hallucinations.
6. **Metadata, Music Style (`bgm_mood`) & Engagement Comment (`comment_prompt`)**:
   - `title`: Crisp natural Title Case YouTube Shorts title under 62 chars — **NO `#shorts` or hashtags in title**. NEVER end with "Explained", NEVER start with "The Physics of..." or "It...". MUST be 100% distinct from every title in the avoid list above!
   - `description`: 1–2 concise natural sentences + hashtags: {desc_tags}
   - `tags`: 6-10 relevant SEO keywords
   - `comment_prompt`: One friendly, curious starter comment (under 110 chars, zero spam/links/subscribe begging) asking viewers a fun question or what extreme phenomenon they want covered next.
   - `bgm_mood`: Choose the SINGLE clean documentary music style that best fits THIS story:
     - `"curious_pizzicato"`: quirky animals, chemistry surprises, unexpected mechanical reveals (plucked strings + crisp mallets)
     - `"suspense_underscore"`: predators, volcanoes, deep-sea danger, high-stakes survival (tense cello + muted pulse)
     - `"sleek_documentary"`: modern physics, engineering, lab wonders, clever mechanisms (clean marimba + warm sub-bass)
     - `"minimal_ambient"`: deep space, cosmic scales, extreme cold/abyss, eerie quiet wonders (warm pads + sparse bells)
     - `"quirky_plucked"`: insects, weird plants, natural evolution, biological tricks (acoustic harp/plucks + light groove)
     - `"mystery_keys"`: ancient planetary mysteries, paradoxes, myth-busting, strange materials (warm felt keys + celesta)

Return ONLY valid JSON matching this exact schema:
{{
  "topic": "short unique topic name",
  "title": "Catchy Human Title Without Hashtags",
  "description": "Short description with hashtags",
  "tags": ["science", "nature"],
  "bgm_mood": "curious_pizzicato",
  "comment_prompt": "Short friendly curiosity question for viewers",
  "topic_fallback_queries": ["scientific backup query 1", "scientific backup query 2", "..."],
  "beats": [
    {{"narration": "When a rogue ant slips inside", "search_query": "army ant colony macro"}},
    {{"narration": "another colony's underground nest,", "search_query": "ant nest soil macro"}},
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

            if not (44 <= word_count <= 78 and len(script.beats) >= 10):
                if attempt < 2:
                    logger.warning(
                        "Script pacing out of bounds (%d beats, %d words); regenerating for Zack-D micro-story pacing...",
                        len(script.beats),
                        word_count,
                    )
                    prompt = (
                        base_prompt
                        + f"\nCORRECTION REQUIRED: Your previous draft had {len(script.beats)} beats and {word_count} words. "
                        "You MUST output 12 to 14 beats and STRICTLY 56 to 72 words TOTAL (~4-6 words per beat).\n"
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
