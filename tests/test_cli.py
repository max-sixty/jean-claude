"""Tests for jean-claude CLI."""

from __future__ import annotations

from click.testing import CliRunner

from jean_claude.cli import cli


def test_help():
    """Test --help flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Gmail, Calendar, Drive, and iMessage" in result.output
    assert "gmail" in result.output
    assert "gcal" in result.output
    assert "gdrive" in result.output
    assert "imessage" in result.output
    assert "auth" in result.output
    assert "status" in result.output


def test_auth_logout_no_token(tmp_path, monkeypatch):
    """Test auth --logout when no token exists."""
    # Point TOKEN_FILE to a non-existent file
    from jean_claude import auth
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "nonexistent.json")

    # Re-import cli to pick up the monkeypatched value
    from jean_claude import cli as cli_module
    monkeypatch.setattr(cli_module, "TOKEN_FILE", tmp_path / "nonexistent.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["auth", "--logout"])
    assert result.exit_code == 0
    assert "Not logged in" in result.output


def test_auth_logout_with_token(tmp_path, monkeypatch):
    """Test auth --logout when token exists."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "test"}')

    from jean_claude import auth, cli as cli_module
    monkeypatch.setattr(auth, "TOKEN_FILE", token_file)
    monkeypatch.setattr(cli_module, "TOKEN_FILE", token_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["auth", "--logout"])
    assert result.exit_code == 0
    assert "Logged out" in result.output
    assert not token_file.exists()


def test_status_no_auth(tmp_path, monkeypatch):
    """Test status when not authenticated."""
    from jean_claude import auth, cli as cli_module
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "nonexistent.json")
    monkeypatch.setattr(cli_module, "TOKEN_FILE", tmp_path / "nonexistent.json")

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Not authenticated" in result.output


def test_completions_bash():
    """Test bash completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "bash"])
    assert result.exit_code == 0
    assert "_JEAN_COMPLETE" in result.output
    assert "complete" in result.output.lower()


def test_completions_zsh():
    """Test zsh completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "_JEAN_COMPLETE" in result.output


def test_completions_fish():
    """Test fish completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "fish"])
    assert result.exit_code == 0
    assert "_JEAN_COMPLETE" in result.output


def test_gmail_help():
    """Test gmail subcommand help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["gmail", "--help"])
    assert result.exit_code == 0
    assert "inbox" in result.output
    assert "search" in result.output
    assert "draft" in result.output


def test_gcal_help():
    """Test gcal subcommand help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["gcal", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "create" in result.output
    assert "search" in result.output


def test_gdrive_help():
    """Test gdrive subcommand help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["gdrive", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "search" in result.output
    assert "upload" in result.output
    assert "download" in result.output


def test_imessage_help():
    """Test imessage subcommand help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["imessage", "--help"])
    assert result.exit_code == 0
    assert "send" in result.output
    assert "chats" in result.output
