#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "google-api-python-client",
#     "google-auth-oauthlib",
# ]
# ///
"""
Shared OAuth authentication for Google APIs.

Run this script to set up credentials:
    uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/auth.py
"""

import json
import logging
import sys
from pathlib import Path

# Configure API logging to stderr so agents can see what's happening
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
logging.getLogger("googleapiclient.discovery").addHandler(_handler)
logging.getLogger("googleapiclient.discovery").setLevel(logging.DEBUG)

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Store credentials in user's home directory
CONFIG_DIR = Path.home() / ".config" / "jean-claude"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"
TOKEN_FILE = CONFIG_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
]


def _run_oauth_flow() -> Credentials:
    """Run OAuth flow to get new credentials."""
    if not CLIENT_SECRET_FILE.exists():
        raise SystemExit(
            f"Missing {CLIENT_SECRET_FILE}\n\n"
            "To create OAuth credentials:\n"
            "1. Go to: https://console.cloud.google.com/apis/credentials\n"
            "2. Click 'Create Credentials' -> 'OAuth client ID'\n"
            "3. Select 'Desktop app' as application type\n"
            "4. Download the JSON file\n"
            f"5. Save it as: {CLIENT_SECRET_FILE}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def get_credentials() -> Credentials:
    """Load credentials, refreshing if needed. Runs OAuth flow if no token exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not TOKEN_FILE.exists():
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
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Token file corrupted ({e}), re-authenticating...", file=sys.stderr)
        TOKEN_FILE.unlink(missing_ok=True)
        return _run_oauth_flow()

    # Try to refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
        except Exception as e:
            print(f"Token refresh failed ({e}), re-authenticating...", file=sys.stderr)
            TOKEN_FILE.unlink(missing_ok=True)
            return _run_oauth_flow()

    return creds


def _save_token(creds: Credentials) -> None:
    """Save credentials to token file with secure permissions."""
    TOKEN_FILE.write_text(json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }))
    TOKEN_FILE.chmod(0o600)


if __name__ == "__main__":
    get_credentials()
    print("OAuth setup complete!")
