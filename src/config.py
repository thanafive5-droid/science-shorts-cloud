"""Configuration and channel definitions for the 4-channel Science Shorts Cloud Factory."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a single YouTube Shorts channel."""

    id: str
    display_name: str
    niche_description: str
    default_hashtags: tuple[str, ...]
    schedule_hours: tuple[int, ...]
    schedule_minute: int
    youtube_handle: str = ""
    nasa_ratio: float = 0.0
    allow_animations: bool = True
    tts_voice: str = "en-US-AndrewMultilingualNeural"
    tts_rate: str = "+5%"
    cut_min_sec: float = 1.0
    cut_max_sec: float = 2.5
    bgm_volume: float = 0.25
    fallback_visual_queries: tuple[str, ...] = ()
    max_daily_uploads: int = 4
    min_interval_hours: float = 3.5
    preferred_ict_hours: tuple[int, ...] = (0, 8, 12, 16)


CHANNELS: dict[str, ChannelConfig] = {
    "physics": ChannelConfig(
        id="physics",
        display_name="Physics",
        niche_description=(
            "Mind-blowing physics phenomena, quantum mechanics, relativity, astrophysics, "
            "thermodynamics, electromagnetism, fluid dynamics, and everyday physics paradoxes."
        ),
        default_hashtags=("#physics", "#science", "#space", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=0,
        youtube_handle="@Quantum_Snap_YT_Physics",
        nasa_ratio=0.0,
        tts_voice="en-US-AndrewMultilingualNeural",
        tts_rate="+5%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.25,
        preferred_ict_hours=(0, 8, 12, 16),
        fallback_visual_queries=(
            "physics experiment magnet",
            "laser light prism",
            "water wave ripple",
            "lightning electricity arc",
            "superconductor laboratory",
        ),
    ),
    "chemistry": ChannelConfig(
        id="chemistry",
        display_name="Chemistry",
        niche_description=(
            "Explosive and surprising chemical reactions, elements, molecular mysteries, "
            "materials science, everyday chemistry secrets, and extreme states of matter."
        ),
        default_hashtags=("#chemistry", "#science", "#experiment", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=15,
        youtube_handle="@ReactionRush-v6u",
        nasa_ratio=0.0,
        tts_voice="en-US-BrianNeural",
        tts_rate="+6%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.26,
        preferred_ict_hours=(0, 4, 8, 12),
        fallback_visual_queries=(
            "chemical reaction laboratory",
            "bismuth crystal rainbow",
            "fire flame combustion",
            "liquid nitrogen vapor",
            "glowing mineral crystal",
        ),
    ),
    "biology": ChannelConfig(
        id="biology",
        display_name="Biology",
        niche_description=(
            "Family-friendly wildlife adaptations, ocean ecosystems, plants & photosynthesis, "
            "space biology, extremophiles, microorganisms, forests, coral reefs, and nature marvels. "
            "Strictly G-rated: avoid graphic medical/surgical/anatomical or sensitive human body topics."
        ),
        default_hashtags=("#biology", "#science", "#nature", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=30,
        youtube_handle="@BioPulse-Daily-yt",
        nasa_ratio=0.0,
        tts_voice="en-US-EmmaMultilingualNeural",
        tts_rate="+5%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.24,
        preferred_ict_hours=(4, 8, 12, 16),
        fallback_visual_queries=(
            "coral reef underwater fish",
            "bioluminescent ocean jellyfish",
            "tropical rainforest wildlife",
            "microscope cell organism",
            "venus flytrap plant nature",
        ),
    ),
    "general_science": ChannelConfig(
        id="general_science",
        display_name="General Science",
        niche_description=(
            "Cross-disciplinary science wonders, Earth & climate phenomena, technology breakthroughs, "
            "space exploration, math in nature, and surprising scientific facts."
        ),
        default_hashtags=("#science", "#facts", "#didyouknow", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=45,
        youtube_handle="@CoreScienceL-v4b",
        nasa_ratio=0.0,
        allow_animations=True,
        tts_voice="en-US-AvaMultilingualNeural",
        tts_rate="+5%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.25,
        preferred_ict_hours=(0, 8, 12, 16),
        fallback_visual_queries=(
            "science laboratory experiment",
            "aurora borealis night sky",
            "volcano lava nature",
            "lightning storm clouds",
            "ice crystal macro",
        ),
    ),
    "earth_science": ChannelConfig(
        id="earth_science",
        display_name="EarthSci",
        niche_description=(
            "Earth science wonders: plate tectonics, volcanoes, earthquakes, ocean currents, "
            "atmosphere, weather systems, glaciers, geology, rock cycles, and Earth's deep interior."
        ),
        default_hashtags=("#earthscience", "#geology", "#earth", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=5,
        youtube_handle="@EarthSciPulse",
        nasa_ratio=0.0,
        allow_animations=True,
        tts_voice="en-US-AndrewMultilingualNeural",
        tts_rate="+4%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.25,
        preferred_ict_hours=(4, 8, 12, 16),
        fallback_visual_queries=(
            "volcano lava eruption",
            "glacier ice canyon",
            "geological rock strata canyon",
            "ocean storm waves cliff",
            "amethyst quartz crystal cave",
        ),
    ),
    "astronomy": ChannelConfig(
        id="astronomy",
        display_name="Astronomy",
        niche_description=(
            "Astronomy & astrophysics: black holes, neutron stars, exoplanets, nebulae, "
            "galaxies, supernovae, solar system moons, James Webb discoveries, and cosmic phenomena."
        ),
        default_hashtags=("#astronomy", "#space", "#cosmos", "#shorts"),
        schedule_hours=(3, 7, 11, 15, 19, 23),
        schedule_minute=20,
        youtube_handle="@AstroSnapDaily-r4l",
        nasa_ratio=0.5,
        allow_animations=True,
        tts_voice="en-US-BrianNeural",
        tts_rate="+4%",
        cut_min_sec=1.0,
        cut_max_sec=2.5,
        bgm_volume=0.26,
        preferred_ict_hours=(0, 4, 8, 12),
        fallback_visual_queries=(
            "spiral galaxy nebula telescope",
            "jupiter planet space",
            "solar flare sun",
            "supernova remnant stars",
            "milky way night sky",
        ),
    ),
}


@dataclass(frozen=True)
class YouTubeCredentials:
    """Per-channel YouTube OAuth2 credentials."""

    client_id: str
    client_secret: str
    refresh_token: str


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # AI & Media APIs
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # Scheduler & Pipeline
    timezone: str = "America/New_York"
    posts_per_day_per_channel: int = 6
    dry_run: bool = False

    # TTS
    tts_voice: str = "en-US-AndrewMultilingualNeural"
    tts_rate: str = "+5%"

    # Paths & Server
    data_dir: Path = Path("/data")
    work_dir: Path = Path("/tmp/shorts_jobs")
    port: int = 8080

    # Per-channel YouTube OAuth credentials
    physics_yt_client_id: str = ""
    physics_yt_client_secret: str = ""
    physics_yt_refresh_token: str = ""

    chemistry_yt_client_id: str = ""
    chemistry_yt_client_secret: str = ""
    chemistry_yt_refresh_token: str = ""

    biology_yt_client_id: str = ""
    biology_yt_client_secret: str = ""
    biology_yt_refresh_token: str = ""

    general_science_yt_client_id: str = ""
    general_science_yt_client_secret: str = ""
    general_science_yt_refresh_token: str = ""

    earth_science_yt_client_id: str = ""
    earth_science_yt_client_secret: str = ""
    earth_science_yt_refresh_token: str = ""

    astronomy_yt_client_id: str = ""
    astronomy_yt_client_secret: str = ""
    astronomy_yt_refresh_token: str = ""

    def get_youtube_credentials(self, channel_id: str) -> Optional[YouTubeCredentials]:
        """Return OAuth credentials for a channel if all three fields are configured."""
        mapping = {
            "physics": (
                self.physics_yt_client_id,
                self.physics_yt_client_secret,
                self.physics_yt_refresh_token,
            ),
            "chemistry": (
                self.chemistry_yt_client_id,
                self.chemistry_yt_client_secret,
                self.chemistry_yt_refresh_token,
            ),
            "biology": (
                self.biology_yt_client_id,
                self.biology_yt_client_secret,
                self.biology_yt_refresh_token,
            ),
            "general_science": (
                self.general_science_yt_client_id,
                self.general_science_yt_client_secret,
                self.general_science_yt_refresh_token,
            ),
            "earth_science": (
                self.earth_science_yt_client_id,
                self.earth_science_yt_client_secret,
                self.earth_science_yt_refresh_token,
            ),
            "astronomy": (
                self.astronomy_yt_client_id,
                self.astronomy_yt_client_secret,
                self.astronomy_yt_refresh_token,
            ),
        }
        cid, csec, rtok = mapping.get(channel_id, ("", "", ""))
        if cid and csec and rtok:
            return YouTubeCredentials(client_id=cid, client_secret=csec, refresh_token=rtok)
        return None
