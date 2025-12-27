"""Phone number utilities shared across messaging modules."""

from __future__ import annotations


def normalize_phone(phone: str) -> str:
    """Normalize phone number to digits only (with leading + if present).

    Used for comparison/matching when we want to ignore formatting differences.
    """
    has_plus = phone.startswith("+")
    digits = "".join(c for c in phone if c.isdigit())
    return f"+{digits}" if has_plus else digits


def strip_formatting(value: str) -> str:
    """Strip common phone formatting characters.

    Returns a string with spaces, dashes, parentheses, and dots removed.
    Used to check if a value looks like a phone number.
    """
    return (
        value.replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
    )


def looks_like_phone(value: str) -> bool:
    """Check if a value looks like a phone number.

    Returns True if value (with formatting stripped) looks like:
    - International format: +1234567890 (+ followed by 7+ digits)
    - Local format: 1234567890 (7+ digits)
    """
    cleaned = strip_formatting(value)

    # International format: + followed by at least 7 digits
    if cleaned.startswith("+") and len(cleaned) >= 8 and cleaned[1:].isdigit():
        return True

    # Local format: at least 7 digits (shortest valid phone numbers)
    if cleaned.isdigit() and len(cleaned) >= 7:
        return True

    return False
