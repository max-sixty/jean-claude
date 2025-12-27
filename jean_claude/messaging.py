"""Shared messaging utilities for iMessage and WhatsApp."""

from __future__ import annotations

from typing import Callable

from .logging import JeanClaudeError
from .phone import looks_like_phone


def disambiguate_chat_matches(
    matches: list[tuple[str, str]], name: str, id_type: str = "ID"
) -> str | None:
    """Return unique chat ID from matches, or raise on ambiguity.

    Args:
        matches: List of (id, display_name) tuples
        name: The name that was searched for (for error messages)
        id_type: How to refer to the ID in error messages (e.g., "chat ID", "ID")

    Returns:
        The chat ID if exactly one match, None if no matches

    Raises:
        JeanClaudeError: If multiple chats match the name
    """
    if not matches:
        return None

    if len(matches) > 1:
        matches_str = "\n".join(f"  - {m[1]} ({m[0]})" for m in matches)
        raise JeanClaudeError(
            f"Multiple chats match '{name}':\n{matches_str}\n"
            f"Use the {id_type} to send to a specific chat."
        )

    return matches[0][0]


def resolve_recipient(
    value: str,
    *,
    is_native_id: Callable[[str], bool],
    find_chat_by_name: Callable[[str], str | None],
    resolve_contact: Callable[[str], str] | None = None,
    service_name: str = "messaging",
) -> str:
    """Resolve a recipient value to a native ID or phone number.

    Tries resolution in order:
    1. Native ID (passes through directly)
    2. Phone number (passes through directly)
    3. Chat name lookup
    4. Contact name lookup (if resolve_contact provided)

    Args:
        value: The recipient value to resolve
        is_native_id: Function to check if value is already a native ID
        find_chat_by_name: Function to look up chat by display name
        resolve_contact: Optional function to resolve contact name to phone
        service_name: Service name for error messages (e.g., "WhatsApp", "iMessage")

    Returns:
        The resolved ID or phone number

    Raises:
        JeanClaudeError: If the value cannot be resolved
    """
    # Native IDs pass through directly
    if is_native_id(value):
        return value

    # Phone numbers pass through directly
    if looks_like_phone(value):
        return value

    # Try chat name lookup
    chat_id = find_chat_by_name(value)
    if chat_id:
        return chat_id

    # Try contact lookup if available
    if resolve_contact is not None:
        return resolve_contact(value)

    raise JeanClaudeError(
        f"Could not resolve '{value}' to a {service_name} recipient.\n"
        f"Use a phone number (+12025551234), "
        f"an ID from '{service_name.lower()} chats', "
        f"or a chat name."
    )
