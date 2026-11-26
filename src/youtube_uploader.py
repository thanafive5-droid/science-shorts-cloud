"""YouTube Data API v3 resumable uploader with per-channel OAuth2 credentials."""

import logging
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import YouTubeCredentials

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeUploader:
    """Uploads rendered Shorts to a specific YouTube channel."""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=3, min=5, max=30))
    def upload_short(
        self,
        creds_cfg: YouTubeCredentials,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str = "public",
    ) -> str:
        """Upload a Short and return the published YouTube video ID."""
        creds = Credentials(
            token=None,
            refresh_token=creds_cfg.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_cfg.client_id,
            client_secret=creds_cfg.client_secret,
            scopes=YOUTUBE_SCOPES,
        )
        creds.refresh(Request())

        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": tags[:15],
                "categoryId": "28",  # Science & Technology
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True,
        )

        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response["id"]
        logger.info("Published YouTube Short: https://youtube.com/shorts/%s", video_id)
        return video_id

    def post_engagement_comment(
        self,
        creds_cfg: YouTubeCredentials,
        video_id: str,
        comment_text: str,
    ) -> bool:
        """Post a non-fatal starter discussion/vote comment to boost retention & subscriber conversion."""
        clean_comment = str(comment_text or "").strip()[:220]
        if not video_id or not clean_comment:
            return False
        try:
            # Omit explicit scopes here so refresh uses whatever scopes the stored token holds cleanly
            creds = Credentials(
                token=None,
                refresh_token=creds_cfg.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_cfg.client_id,
                client_secret=creds_cfg.client_secret,
            )
            creds.refresh(Request())
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {"snippet": {"textOriginal": clean_comment}},
                    }
                },
            ).execute()
            logger.info("Posted engagement starter comment on %s", video_id)
            return True
        except Exception as exc:
            logger.warning("Non-fatal: could not post engagement comment on %s: %s", video_id, exc)
            return False
