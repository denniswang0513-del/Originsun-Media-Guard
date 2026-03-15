"""
drive_sync.py
─────────────
Uploads an HTML report file to Google Drive and returns a shareable link.

Requires:
  - google-api-python-client
  - google-auth-httplib2
  - google-auth-oauthlib
  - credentials/credentials.json  (obtained from Google Cloud Console)

Usage:
  url = upload_to_drive(html_path, folder_id="YOUR_GDRIVE_FOLDER_ID")
"""

import os
from typing import Optional

# Credentials are stored relative to this file
_CREDS_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials")
_CREDS_FILE   = os.path.join(_CREDS_DIR, "credentials.json")
_TOKEN_FILE   = os.path.join(_CREDS_DIR, "token.json")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_service():  # type: ignore
    """Authenticate and return a Google Drive service object."""
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"Google API libraries not installed: {e}\nRun: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")

    if not os.path.exists(_CREDS_FILE):
        raise FileNotFoundError(
            f"Google credentials not found at: {_CREDS_FILE}\n"
            "Please download credentials.json from Google Cloud Console and place it in the credentials/ directory."
        )

    creds = None
    if os.path.exists(_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(_TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path: str, folder_id: Optional[str] = None) -> str:
    """
    Upload `file_path` to Google Drive (inside `folder_id` if provided).

    Returns a publicly accessible view URL like:
        https://drive.google.com/file/d/<FILE_ID>/view
    """
    try:
        from googleapiclient.http import MediaFileUpload  # type: ignore
    except ImportError:
        raise RuntimeError("google-api-python-client not installed.")

    service = _get_service()

    file_metadata: dict = {"name": os.path.basename(file_path)}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(file_path, mimetype="text/html", resumable=True)
    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name"
    ).execute()

    file_id = created.get("id", "")

    # Make publicly readable
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"
