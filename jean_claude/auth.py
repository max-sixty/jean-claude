"""Shared OAuth authentication for Google APIs."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .logging import LoggingHttp, get_logger

logger = get_logger(__name__)

# Store credentials in user's home directory
CONFIG_DIR = Path.home() / ".config" / "jean-claude"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"
TOKEN_FILE = CONFIG_DIR / "token.json"

# Embedded OAuth credentials for public distribution.
# These are inherently non-secret for desktop/CLI apps per Google's OAuth model.
# User tokens are what must be protected (stored with 0600 permissions).
# Users can override by placing their own client_secret.json in CONFIG_DIR.
EMBEDDED_CLIENT_CONFIG = {
    "installed": {
        "client_id": "632159173278-jdi7d5i4aldosvhu4vvu2hsck4fusg00.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

SCOPES_FULL = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.profile",
]

SCOPES_READONLY = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/userinfo.profile",  # For display name in From header
]


def _run_oauth_flow(readonly: bool = False) -> Credentials:
    """Run OAuth flow to get new credentials.

    Uses user-provided client_secret.json if present, otherwise falls back
    to embedded credentials.
    """
    scopes = SCOPES_READONLY if readonly else SCOPES_FULL
    scope_type = "read-only" if readonly else "full"
    logger.info(f"Starting OAuth flow ({scope_type} access)")
    if CLIENT_SECRET_FILE.exists():
        logger.info(f"Using custom credentials from {CLIENT_SECRET_FILE}")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes)
    else:
        logger.debug("Using embedded OAuth credentials")
        flow = InstalledAppFlow.from_client_config(EMBEDDED_CLIENT_CONFIG, scopes)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    logger.info("OAuth flow complete")
    return creds


def get_credentials() -> Credentials:
    """Load credentials, refreshing if needed. Runs OAuth flow if no token exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not TOKEN_FILE.exists():
        logger.debug("No token file found")
        return _run_oauth_flow()

    # Try to load existing token
    try:
        token_data = json.loads(TOKEN_FILE.read_text())
        creds = Credentials(
            token=token_data["token"],
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=token_data["scopes"],
        )
        logger.debug("Token loaded from file")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Token file corrupted ({e}), re-authenticating...")
        TOKEN_FILE.unlink(missing_ok=True)
        return _run_oauth_flow()

    # Try to refresh if expired
    if creds.expired and creds.refresh_token:
        logger.debug("Token expired, refreshing")
        try:
            creds.refresh(Request())
            _save_token(creds)
            logger.info("Token refreshed")
        except Exception as e:
            logger.warning(f"Token refresh failed ({e}), re-authenticating...")
            TOKEN_FILE.unlink(missing_ok=True)
            return _run_oauth_flow()

    return creds


def _save_token(creds: Credentials) -> None:
    """Save credentials to token file with secure permissions."""
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes),
            }
        )
    )
    TOKEN_FILE.chmod(0o600)


def run_auth(readonly: bool = False) -> None:
    """Run OAuth authentication flow.

    Args:
        readonly: If True, request only read-only scopes.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Check if re-authenticating with different scope level
    if TOKEN_FILE.exists():
        try:
            token_data = json.loads(TOKEN_FILE.read_text())
            existing_scopes = set(token_data.get("scopes", []))
            requested_scopes = set(SCOPES_READONLY if readonly else SCOPES_FULL)

            if existing_scopes != requested_scopes:
                logger.warning("Scope change detected, re-authenticating...")
                TOKEN_FILE.unlink()
        except (json.JSONDecodeError, KeyError):
            pass

    if TOKEN_FILE.exists():
        print(
            "Already authenticated. Delete ~/.config/jean-claude/token.json to re-authenticate."
        )
        return

    _run_oauth_flow(readonly=readonly)
    scope_type = "read-only" if readonly else "full access"
    print(f"OAuth setup complete! ({scope_type})")


def build_service(service_name: str, version: str):
    """Build a Google API service with automatic request logging.

    All API requests (GET, POST, PUT, DELETE, PATCH) are logged automatically.

    Args:
        service_name: API service name (e.g., "gmail", "calendar", "drive")
        version: API version (e.g., "v1", "v3")

    Returns:
        Google API service resource with logging enabled.
    """
    import google_auth_httplib2
    import httplib2

    creds = get_credentials()
    authorized_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
    logging_http = LoggingHttp(authorized_http)
    return build(service_name, version, http=logging_http)
