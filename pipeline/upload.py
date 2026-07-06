"""Optional YouTube upload via the official Data API v3.

Setup (one-time, documented in README):
1. Google Cloud Console -> create project -> enable "YouTube Data API v3".
2. OAuth consent screen -> add your channel's Google account as test user.
3. Credentials -> OAuth client ID (Desktop app) -> download client_secret.json
   into ./secrets/client_secret.json.
4. First run opens a browser for consent and caches ./secrets/token.json.

Videos are uploaded PRIVATE by default — review before publishing. The
synthetic-media disclosure flag is always set, per YouTube's AI policy.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("upload")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "./secrets/client_secret.json")
TOKEN_PATH = os.environ.get("YT_TOKEN", "./secrets/token.json")


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                raise RuntimeError(
                    f"missing {CLIENT_SECRET}; follow the OAuth setup in README.md"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
    return creds


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    thumbnail_path: str | None = None,
    privacy: str = "private",
) -> str:
    """Upload and return the video ID."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=_credentials())
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:30],
            "categoryId": "22",  # People & Blogs
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # mandatory AI disclosure
        },
    }
    media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("upload %d%%", int(status.progress() * 100))
    video_id = response["id"]
    logger.info("uploaded: https://youtu.be/%s (privacy=%s)", video_id, privacy)

    if thumbnail_path and os.path.exists(thumbnail_path):
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
        ).execute()
        logger.info("thumbnail set")
    return video_id
