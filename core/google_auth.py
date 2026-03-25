"""
Google OAuth token verification module.
Uses Google Identity Services (GIS) credential flow:
  - Frontend sends Google ID Token (RS256 JWT)
  - Backend verifies signature against Google's public keys
  - No client_secret needed, no redirect URI required
"""

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Reuse HTTP session for Google public key fetching (connection pooling)
_transport = google_requests.Request()


class GoogleTokenError(Exception):
    """Raised when Google ID token verification fails."""
    pass


def verify_google_id_token(credential: str, client_id: str) -> dict:
    """
    Verify a Google ID Token and return the decoded claims.

    Args:
        credential: The Google ID Token JWT string from GIS callback.
        client_id: Our Google OAuth Client ID (audience check).

    Returns:
        dict with keys: sub, email, email_verified, name, picture, hd, etc.

    Raises:
        GoogleTokenError on any verification failure.
    """
    if not client_id:
        raise GoogleTokenError("Google OAuth client_id not configured")

    try:
        idinfo = id_token.verify_oauth2_token(
            credential,
            _transport,
            client_id,
        )
    except ValueError as e:
        raise GoogleTokenError(f"Invalid Google token: {e}")

    if idinfo.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleTokenError("Invalid token issuer")

    return idinfo
