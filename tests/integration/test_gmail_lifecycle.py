"""Gmail integration tests - full lifecycle with real API calls.

These tests send a real email to yourself, then perform various operations
on it to verify the CLI works end-to-end with the Gmail API.

Run with: uv run pytest -m integration

Note: All tests share one test message (module-scoped fixture) to minimize
API calls. Tests must be idempotent and restore state after modifications.
"""

from __future__ import annotations

import json
import time

import pytest

from jean_claude.cli import cli

pytestmark = pytest.mark.integration


class TestGmailMessageOperations:
    """Test basic message operations on a real email."""

    def test_message_appears_in_inbox(self, runner, test_message, test_subject):
        """Verify the test message is in inbox."""
        result = runner.invoke(
            cli, ["gmail", "search", f"in:inbox subject:{test_subject}"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["messages"]) >= 1
        assert any(m["id"] == test_message for m in data["messages"])

    def test_get_message(self, runner, test_message):
        """Verify we can fetch the full message."""
        result = runner.invoke(cli, ["gmail", "get", test_message])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["id"] == test_message
        assert "file" in data  # Body written to file

    def test_star_and_unstar(self, runner, test_message):
        """Test starring and unstarring a message."""
        # Star
        result = runner.invoke(cli, ["gmail", "star", test_message])
        assert result.exit_code == 0

        # Verify starred
        result = runner.invoke(cli, ["gmail", "get", test_message])
        data = json.loads(result.stdout)
        assert "STARRED" in data["labels"]

        # Unstar (restore state for other tests)
        result = runner.invoke(cli, ["gmail", "unstar", test_message])
        assert result.exit_code == 0

        # Verify unstarred
        result = runner.invoke(cli, ["gmail", "get", test_message])
        data = json.loads(result.stdout)
        assert "STARRED" not in data["labels"]

    def test_mark_read_unread(self, runner, test_message, test_thread):
        """Test marking thread read and unread."""
        # Mark read (operates on threads)
        result = runner.invoke(cli, ["gmail", "mark-read", test_thread])
        assert result.exit_code == 0

        # Verify read (no UNREAD label)
        result = runner.invoke(cli, ["gmail", "get", test_message])
        data = json.loads(result.stdout)
        assert "UNREAD" not in data["labels"]

        # Mark unread
        result = runner.invoke(cli, ["gmail", "mark-unread", test_thread])
        assert result.exit_code == 0

        # Verify unread
        result = runner.invoke(cli, ["gmail", "get", test_message])
        data = json.loads(result.stdout)
        assert "UNREAD" in data["labels"]

        # Restore state: mark as read
        runner.invoke(cli, ["gmail", "mark-read", test_thread])


class TestGmailArchive:
    """Test archive and unarchive operations."""

    def test_archive_and_unarchive(self, runner, test_message, test_thread, test_subject):
        """Test archiving and unarchiving a thread."""
        # Archive (operates on threads)
        result = runner.invoke(cli, ["gmail", "archive", test_thread])
        assert result.exit_code == 0

        # Verify not in inbox
        # Gmail may take a moment to process label changes
        time.sleep(1)
        result = runner.invoke(
            cli, ["gmail", "search", f"in:inbox subject:{test_subject}"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        matching = [m for m in data["messages"] if m["id"] == test_message]
        assert len(matching) == 0, "Message should not be in inbox after archive"

        # Unarchive (restore state)
        result = runner.invoke(cli, ["gmail", "unarchive", test_thread])
        assert result.exit_code == 0

        # Verify back in inbox
        time.sleep(1)
        result = runner.invoke(
            cli, ["gmail", "search", f"in:inbox subject:{test_subject}"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        matching = [m for m in data["messages"] if m["id"] == test_message]
        assert len(matching) == 1, "Message should be in inbox after unarchive"


class TestGmailDraftOperations:
    """Test draft creation and management."""

    def test_reply_draft(self, runner, test_message, draft_cleanup):
        """Test creating a reply draft."""
        reply_data = json.dumps({"body": "This is an automated reply."})
        result = runner.invoke(
            cli, ["gmail", "draft", "reply", test_message], input=reply_data
        )
        assert result.exit_code == 0

        # List drafts and find our reply
        result = runner.invoke(cli, ["gmail", "draft", "list", "-n", "10"])
        assert result.exit_code == 0
        drafts = json.loads(result.stdout)

        # Find the reply draft by snippet (contains our reply text)
        reply_draft = None
        for draft in drafts:
            if "automated reply" in draft["snippet"]:
                reply_draft = draft
                break

        assert reply_draft is not None, "Reply draft not found"
        draft_cleanup(reply_draft["id"])

    def test_forward_draft(self, runner, test_message, my_email, draft_cleanup):
        """Test creating a forward draft."""
        forward_data = json.dumps({
            "to": my_email,
            "body": "FYI - forwarding this test message.",
        })
        result = runner.invoke(
            cli, ["gmail", "draft", "forward", test_message], input=forward_data
        )
        assert result.exit_code == 0

        # List drafts and find our forward
        result = runner.invoke(cli, ["gmail", "draft", "list", "-n", "10"])
        assert result.exit_code == 0
        drafts = json.loads(result.stdout)

        # Find the forward draft by snippet (contains our forward text)
        forward_draft = None
        for draft in drafts:
            if "FYI - forwarding" in draft["snippet"]:
                forward_draft = draft
                break

        assert forward_draft is not None, "Forward draft not found"
        draft_cleanup(forward_draft["id"])


class TestGmailSearch:
    """Test search functionality."""

    def test_search_by_subject(self, runner, test_subject):
        """Test searching by subject."""
        result = runner.invoke(cli, ["gmail", "search", f"subject:{test_subject}"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["messages"]) >= 1

    def test_inbox_command(self, runner):
        """Test the inbox command returns threads."""
        result = runner.invoke(cli, ["gmail", "inbox", "-n", "5"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        # Should have threads key (may be empty if inbox is empty)
        assert "threads" in data
