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

    def test_archive_and_unarchive(
        self, runner, test_message, test_thread, test_subject
    ):
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
        drafts = json.loads(result.stdout)["drafts"]

        # Find the reply draft by snippet (contains our reply text)
        reply_draft = None
        for draft in drafts:
            if "automated reply" in draft["snippet"]:
                reply_draft = draft
                break

        assert reply_draft is not None, "Reply draft not found"
        draft_cleanup(reply_draft["id"])

    def test_forward_draft_includes_original_message(
        self, runner, test_message, my_email, draft_cleanup
    ):
        """Test that forward draft includes original message body and proper From header."""
        forward_data = json.dumps(
            {
                "to": my_email,
                "body": "FYI - forwarding this test message.",
            }
        )
        result = runner.invoke(
            cli, ["gmail", "draft", "forward", test_message], input=forward_data
        )
        assert result.exit_code == 0

        # List drafts and find our forward
        result = runner.invoke(cli, ["gmail", "draft", "list", "-n", "10"])
        assert result.exit_code == 0
        drafts = json.loads(result.stdout)["drafts"]

        # Find the forward draft by snippet (contains our forward text)
        forward_draft = None
        for draft in drafts:
            if "FYI - forwarding" in draft["snippet"]:
                forward_draft = draft
                break

        assert forward_draft is not None, "Forward draft not found"
        draft_id = forward_draft["id"]
        draft_cleanup(draft_id)

        # Get full draft content to verify original message is included
        result = runner.invoke(cli, ["gmail", "draft", "get", draft_id])
        assert result.exit_code == 0
        get_result = json.loads(result.stdout)
        draft_file = get_result["file"]

        with open(draft_file) as f:
            draft_data = json.load(f)

        body = draft_data["body"]

        # Verify the forwarded message separator is present
        assert "---------- Forwarded message ----------" in body, (
            "Forward draft missing forwarded message separator"
        )

        # Verify original message body is included (from test fixture)
        assert "automated integration test message" in body, (
            "Forward draft missing original message body"
        )

        # Verify From header has display name (not just email)
        # Format should be "Name <email>" or similar with a name
        from_addr = draft_data["from"]
        assert "<" in from_addr and ">" in from_addr, (
            f"From header missing display name format: {from_addr}"
        )

    def test_draft_create_has_from_header(self, runner, my_email, draft_cleanup):
        """Test that draft create sets proper From header with display name."""
        draft_data = json.dumps(
            {
                "to": my_email,
                "subject": "Test From Header",
                "body": "Testing From header format.",
            }
        )
        result = runner.invoke(cli, ["gmail", "draft", "create"], input=draft_data)
        assert result.exit_code == 0

        # List drafts and find ours
        result = runner.invoke(cli, ["gmail", "draft", "list", "-n", "10"])
        assert result.exit_code == 0
        drafts = json.loads(result.stdout)["drafts"]

        test_draft = None
        for draft in drafts:
            if "Testing From header" in draft["snippet"]:
                test_draft = draft
                break

        assert test_draft is not None, "Test draft not found"
        draft_id = test_draft["id"]
        draft_cleanup(draft_id)

        # Get full draft content
        result = runner.invoke(cli, ["gmail", "draft", "get", draft_id])
        assert result.exit_code == 0
        get_result = json.loads(result.stdout)
        draft_file = get_result["file"]

        with open(draft_file) as f:
            draft_data = json.load(f)

        # Verify From header has display name format
        from_addr = draft_data["from"]
        assert "<" in from_addr and ">" in from_addr, (
            f"From header missing display name format: {from_addr}"
        )

    def test_draft_update_preserves_from_header(self, runner, my_email, draft_cleanup):
        """Test that draft update preserves From header when not explicitly changed."""
        # Create initial draft
        draft_data = json.dumps(
            {
                "to": my_email,
                "subject": "Test Update Preserves From",
                "body": "Initial body.",
            }
        )
        result = runner.invoke(cli, ["gmail", "draft", "create"], input=draft_data)
        assert result.exit_code == 0

        # Find the draft
        result = runner.invoke(cli, ["gmail", "draft", "list", "-n", "10"])
        assert result.exit_code == 0
        drafts = json.loads(result.stdout)["drafts"]

        test_draft = None
        for draft in drafts:
            if "Initial body" in draft["snippet"]:
                test_draft = draft
                break

        assert test_draft is not None, "Test draft not found"
        draft_id = test_draft["id"]
        draft_cleanup(draft_id)

        # Get original From header
        result = runner.invoke(cli, ["gmail", "draft", "get", draft_id])
        assert result.exit_code == 0
        get_result = json.loads(result.stdout)
        draft_file = get_result["file"]

        with open(draft_file) as f:
            original_data = json.load(f)

        original_from = original_data["from"]

        # Update the draft body only (not From)
        update_data = json.dumps({"body": "Updated body content."})
        result = runner.invoke(
            cli, ["gmail", "draft", "update", draft_id], input=update_data
        )
        assert result.exit_code == 0

        # Get updated draft and verify From is preserved
        result = runner.invoke(cli, ["gmail", "draft", "get", draft_id])
        assert result.exit_code == 0
        get_result = json.loads(result.stdout)
        draft_file = get_result["file"]

        with open(draft_file) as f:
            updated_data = json.load(f)

        updated_from = updated_data["from"]

        assert original_from == updated_from, (
            f"From header changed during update: '{original_from}' -> '{updated_from}'"
        )

        # Verify body was updated
        assert "Updated body content" in updated_data["body"]


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
