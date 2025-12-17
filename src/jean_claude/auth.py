"""Shared OAuth authentication for Google APIs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

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
    """Run OAuth flow to get new credentials.

    Uses user-provided client_secret.json if present, otherwise falls back
    to embedded credentials.
    """
    if CLIENT_SECRET_FILE.exists():
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_config(EMBEDDED_CLIENT_CONFIG, SCOPES)
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


def main() -> None:
    """CLI entry point for running auth setup."""
    get_credentials()
    print("OAuth setup complete!")


if __name__ == "__main__":
    main()
