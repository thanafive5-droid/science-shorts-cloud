"""One-time helper to generate a YouTube OAuth2 refresh token for a channel.

Usage:
    pip install google-auth-oauthlib
    python scripts/get_youtube_token.py path/to/client_secret.json

Paste the printed CLIENT_ID, CLIENT_SECRET, and REFRESH_TOKEN into your
Railway / Render / Cloud VPS environment variables.
"""

import json
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/get_youtube_token.py <client_secret.json>")
        sys.exit(1)

    secret_path = Path(sys.argv[1])
    if not secret_path.exists():
        print(f"File not found: {secret_path}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    raw = json.loads(secret_path.read_text(encoding="utf-8"))
    client_info = raw.get("installed") or raw.get("web") or {}

    print("\n=== Copy these into your Cloud Environment Variables ===")
    print(f"CLIENT_ID={client_info.get('client_id', '')}")
    print(f"CLIENT_SECRET={client_info.get('client_secret', '')}")
    print(f"REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
