"""Tests for applescript module."""

from jean_claude.applescript import _parse_applescript_error


class TestParseApplescriptError:
    """Tests for _parse_applescript_error function."""

    def test_reminders_list_not_found(self):
        """Reminders list not found error is parsed correctly."""
        stderr = 'execution error: Reminders got an error: Can\'t get list "NonExistent". (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Reminders: List not found: NonExistent"

    def test_reminders_reminder_not_found(self):
        """Reminders reminder not found error is parsed correctly."""
        stderr = 'execution error: Reminders got an error: Can\'t get reminder id "x-apple-reminder://invalid". (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Reminders: Reminder not found: x-apple-reminder://invalid"

    def test_messages_chat_not_found(self):
        """Messages chat not found error is parsed correctly."""
        stderr = 'execution error: Messages got an error: Can\'t get chat id "invalid-chat-id". (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Messages: Chat not found: invalid-chat-id"

    def test_messages_buddy_not_found(self):
        """Messages buddy not found error is parsed correctly."""
        stderr = 'execution error: Messages got an error: Can\'t get buddy "+15551234567". (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Messages: Buddy not found: +15551234567"

    def test_contacts_person_not_found(self):
        """Contacts person not found error is parsed correctly."""
        stderr = 'execution error: Contacts got an error: Can\'t get person "Nobody". (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Contacts: Person not found: Nobody"

    def test_type_conversion_error(self):
        """Type conversion error is parsed correctly."""
        stderr = 'execution error: Reminders got an error: Can\'t make "invalid" into type reference. (-1700)'
        msg = _parse_applescript_error(stderr)
        assert msg == "Reminders: Invalid reference: invalid"

    def test_permission_not_allowed(self):
        """Permission 'not allowed' error shows guidance."""
        stderr = "execution error: Messages got an error: Messages is not allowed assistive access. (-1719)"
        msg = _parse_applescript_error(stderr)
        assert "Automation permission required" in msg
        assert "System Preferences" in msg

    def test_permission_assistive_in_message(self):
        """Permission keywords in error message trigger guidance."""
        stderr = "execution error: System Events got an error: osascript is not allowed to send keystrokes. (-1743)"
        msg = _parse_applescript_error(stderr)
        # "not allowed" is in the message, so it triggers permission guidance
        assert "Automation permission required" in msg
        assert "System Preferences" in msg

    def test_general_app_error(self):
        """General app error is cleaned up."""
        stderr = 'execution error: Calendar got an error: The event "Meeting" wasn\'t found. (-1728)'
        msg = _parse_applescript_error(stderr)
        assert msg == 'Calendar: The event "Meeting" wasn\'t found'

    def test_execution_error_without_code(self):
        """Execution error without error code falls back correctly."""
        stderr = "execution error: Something went wrong"
        msg = _parse_applescript_error(stderr)
        assert msg == "AppleScript: Something went wrong"

    def test_unknown_error_format(self):
        """Unknown error format is returned with prefix."""
        stderr = "some random error message"
        msg = _parse_applescript_error(stderr)
        assert msg == "AppleScript error: some random error message"

    def test_empty_stderr(self):
        """Empty stderr returns generic message."""
        msg = _parse_applescript_error("")
        assert msg == "AppleScript error: Unknown error"

    def test_whitespace_only_stderr(self):
        """Whitespace-only stderr returns generic message."""
        msg = _parse_applescript_error("   \n  ")
        assert msg == "AppleScript error: Unknown error"

    def test_error_with_trailing_period(self):
        """Error message with trailing period is handled."""
        stderr = (
            'execution error: Reminders got an error: Can\'t get list "Work". (-1728)'
        )
        msg = _parse_applescript_error(stderr)
        assert msg == "Reminders: List not found: Work"

    def test_error_without_trailing_period(self):
        """Error message without trailing period is handled."""
        stderr = (
            'execution error: Reminders got an error: Can\'t get list "Home" (-1728)'
        )
        msg = _parse_applescript_error(stderr)
        assert msg == "Reminders: List not found: Home"
