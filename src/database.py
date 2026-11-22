"""SQLite persistence for topic deduplication and post history."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class FactoryDatabase:
    """Thread-safe SQLite wrapper for tracking topics and uploaded Shorts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS used_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    normalized_topic TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(channel_id, normalized_topic)
                );

                CREATE TABLE IF NOT EXISTS post_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    youtube_video_id TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    duration_sec REAL,
                    clip_count INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS used_clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(channel_id, clip_id)
                );
                """
            )

    def get_recent_topics(self, channel_id: str, limit: int = 100) -> list[str]:
        """Return recent topics for a channel to prevent repetition."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT topic FROM used_topics WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
                (channel_id, limit),
            ).fetchall()
        return [row["topic"] for row in rows]

    def get_recent_clip_ids(self, channel_id: str, limit: int = 500) -> set[str]:
        """Return recently used stock clip IDs for this channel so footage never repeats across Shorts."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT clip_id FROM used_clips WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
                (channel_id, limit),
            ).fetchall()
        return {row["clip_id"] for row in rows}

    def mark_clips_used(self, channel_id: str, clip_ids: list[str]) -> None:
        """Persist used stock clip IDs for deduplication across future runs."""
        if not clip_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO used_clips (channel_id, clip_id, created_at) VALUES (?, ?, ?)",
                [(channel_id, cid, now) for cid in clip_ids],
            )

    def mark_topic_used(self, channel_id: str, topic: str) -> None:
        """Record a topic as used for a channel."""
        normalized = " ".join(topic.lower().strip().split())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO used_topics (channel_id, topic, normalized_topic, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (channel_id, topic.strip(), normalized, now),
            )

    def record_post(
        self,
        channel_id: str,
        topic: str,
        title: str,
        status: str,
        youtube_video_id: str | None = None,
        error_message: str | None = None,
        duration_sec: float | None = None,
        clip_count: int | None = None,
    ) -> None:
        """Log a completed or failed Short generation/upload."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO post_history (
                    channel_id, topic, title, youtube_video_id,
                    status, error_message, duration_sec, clip_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    topic,
                    title,
                    youtube_video_id,
                    status,
                    error_message,
                    duration_sec,
                    clip_count,
                    now,
                ),
            )

    def can_post_now(
        self,
        channel_id: str,
        min_interval_hours: float = 3.5,
        max_per_24h: int = 6,
    ) -> tuple[bool, str]:
        """Enforce <=6 uploads/24h and >=3.5h spacing per channel (protects YouTube quota)."""
        now = datetime.now(timezone.utc)
        since_24h = (now - timedelta(hours=24)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT created_at FROM post_history
                WHERE channel_id = ? AND status = 'uploaded' AND created_at >= ?
                ORDER BY id DESC
                """,
                (channel_id, since_24h),
            ).fetchall()

        count_24h = len(rows)
        if count_24h >= max_per_24h:
            return False, f"daily_cap_reached ({count_24h}/{max_per_24h} in last 24h)"

        if rows:
            last_dt = datetime.fromisoformat(rows[0]["created_at"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_hours = (now - last_dt).total_seconds() / 3600.0
            if elapsed_hours < min_interval_hours:
                return False, f"too_soon ({elapsed_hours:.2f}h < {min_interval_hours}h)"

        return True, f"ok ({count_24h}/{max_per_24h} in last 24h)"

    def get_status_summary(self) -> dict[str, Any]:
        """Return aggregate counts and latest posts for the /status endpoint."""
        today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT channel_id,
                       SUM(CASE WHEN status = 'uploaded' THEN 1 ELSE 0 END) AS uploaded_total,
                       SUM(CASE WHEN status = 'dry_run' THEN 1 ELSE 0 END) AS dry_run_total,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_total,
                       SUM(CASE WHEN created_at LIKE ? AND status IN ('uploaded', 'dry_run') THEN 1 ELSE 0 END) AS today_count
                FROM post_history
                GROUP BY channel_id
                """,
                (f"{today_prefix}%",),
            ).fetchall()

            recent = conn.execute(
                """
                SELECT channel_id, title, youtube_video_id, status, duration_sec, clip_count, error_message, created_at
                FROM post_history
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()

        return {
            "channels": [dict(row) for row in totals],
            "recent_posts": [dict(row) for row in recent],
        }
