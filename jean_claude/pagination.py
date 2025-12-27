"""Shared pagination utilities for API responses."""

from __future__ import annotations


def paginated_output(
    key: str, items: list, next_page_token: str | None = None
) -> dict:
    """Build paginated output dict with optional nextPageToken.

    Args:
        key: The key name for the items list (e.g., "messages", "events", "files")
        items: The list of items to include
        next_page_token: Optional pagination token for fetching more results

    Returns:
        Dict with items under the specified key, and optionally nextPageToken
    """
    output: dict = {key: items}
    if next_page_token:
        output["nextPageToken"] = next_page_token
    return output
