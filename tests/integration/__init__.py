"""Integration tests for jean-claude.

These tests require valid Google OAuth credentials and make real API calls.
They are skipped by default and must be run explicitly:

    uv run pytest -m integration

Before running:
1. Ensure you have valid credentials: jean-claude status
2. Run from project root (tests create .tmp/ files)
"""
