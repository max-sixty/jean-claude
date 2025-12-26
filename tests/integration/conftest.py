"""Shared fixtures for integration tests."""

from __future__ import annotations

import json
import time
import uuid

import pytest
from click.testing import CliRunner

from jean_claude.auth import build_service
from jean_claude.cli import cli


@pytest.fixture(scope="session")
def runner():
    """Shared CLI runner."""
    return CliRunner()


@pytest.fixture(scope="session")
def my_email(runner):
    """Get the authenticated user's email address.

    This also serves as a credential check - if not authenticated,
    the test session fails fast with a clear message.
    """
    try:
        gmail = build_service("gmail", "v1")
        profile = gmail.users().getProfile(userId="me").execute()
        return profile["emailAddress"]
    except Exception as e:
        pytest.fail(
            f"Gmail API authentication failed: {e}\n\n"
            "Integration tests require valid credentials.\n"
            "Run 'jean-claude auth' first, then 'jean-claude status' to verify."
        )


@pytest.fixture(scope="module")
def test_subject():
    """Unique subject for this test run to identify test messages."""
    return f"jean-claude-integration-test-{uuid.uuid4()}"


def poll_for_message(runner, query: str, timeout: float = 30.0) -> dict:
    """Poll until a message matching query appears.

    Raises AssertionError if timeout reached without finding a message.
    """
    start = time.time()
    while time.time() - start < timeout:
        result = runner.invoke(cli, ["gmail", "search", query, "-n", "1"])
        if result.exit_code == 0:
            data = json.loads(result.stdout)
            if data["messages"]:
                return data["messages"][0]
        time.sleep(2)
    raise AssertionError(f"No message found for query '{query}' within {timeout}s")


@pytest.fixture
def draft_cleanup(runner):
    """Track drafts created during a test and delete them on teardown.

    Usage:
        def test_something(draft_cleanup):
            # create a draft...
            draft_cleanup(draft_id)  # register for cleanup
    """
    draft_ids = []

    def register(draft_id: str):
        draft_ids.append(draft_id)

    yield register

    for draft_id in draft_ids:
        runner.invoke(cli, ["gmail", "draft", "delete", draft_id])


@pytest.fixture(scope="module")
def sent_message(runner, my_email, test_subject):
    """Send a test email to self, trash by subject on teardown.

    Cleanup searches by subject, so it works even if downstream fixtures
    (like test_message) fail to find the message ID.
    """
    # Create draft
    draft_data = json.dumps(
        {
            "to": my_email,
            "subject": test_subject,
            "body": "This is an automated integration test message.\n\nIt will be trashed after the test completes.",
        }
    )
    result = runner.invoke(cli, ["gmail", "draft", "create"], input=draft_data)
    assert result.exit_code == 0, f"Failed to create draft: {result.output}"

    # draft create now returns JSON with the draft ID
    draft_response = json.loads(result.stdout)
    draft_id = draft_response["id"]

    # Send the draft
    result = runner.invoke(cli, ["gmail", "draft", "send", draft_id])
    assert result.exit_code == 0, f"Failed to send draft: {result.output}"

    yield test_subject

    # Cleanup: trash any messages matching our test subject
    result = runner.invoke(
        cli, ["gmail", "search", f"subject:{test_subject}", "-n", "10"]
    )
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        for msg in data["messages"]:
            runner.invoke(cli, ["gmail", "trash", msg["id"]])


@pytest.fixture(scope="module")
def test_message(runner, sent_message):
    """Wait for the sent message to appear and return its ID."""
    message = poll_for_message(runner, f"subject:{sent_message}", timeout=30.0)
    return message["id"]


@pytest.fixture(scope="module")
def test_thread(runner, sent_message):
    """Wait for the sent message and return its thread ID.

    Some commands (archive, mark-read) operate on threads, not messages.
    """
    message = poll_for_message(runner, f"subject:{sent_message}", timeout=30.0)
    return message["threadId"]
