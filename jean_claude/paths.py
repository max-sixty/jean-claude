"""XDG-compliant storage paths for jean-claude.

Directory layout follows XDG Base Directory Specification:
- ~/.config/jean-claude/     Config and credentials (persistent)
- ~/.local/share/jean-claude/ User data (persistent)
- ~/.cache/jean-claude/       Cached/re-fetchable data (clearable)

See: https://specifications.freedesktop.org/basedir-spec/latest/
"""

from pathlib import Path

# Base directories
CONFIG_DIR = Path.home() / ".config" / "jean-claude"
DATA_DIR = Path.home() / ".local" / "share" / "jean-claude"
CACHE_DIR = Path.home() / ".cache" / "jean-claude"

# Config: credentials and auth state
TOKEN_FILE = CONFIG_DIR / "token.json"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"
WHATSAPP_CONFIG_DIR = CONFIG_DIR / "whatsapp"

# Data: persistent user data
WHATSAPP_DATA_DIR = DATA_DIR / "whatsapp"
WHATSAPP_MEDIA_DIR = WHATSAPP_DATA_DIR / "media"

# Cache: re-fetchable content (can be cleared without data loss)
EMAIL_CACHE_DIR = CACHE_DIR / "emails"
DRAFT_CACHE_DIR = CACHE_DIR / "drafts"
ATTACHMENT_CACHE_DIR = CACHE_DIR / "attachments"
DRIVE_CACHE_DIR = CACHE_DIR / "drive"
