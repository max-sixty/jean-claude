"""Tests for jean-claude CLI."""

from __future__ import annotations

from click.testing import CliRunner

from jean_claude.cli import cli


def test_help():
    """Test --help flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Gmail, Calendar, Drive, iMessage, and WhatsApp" in result.output
    assert "gmail" in result.output
    assert "gcal" in result.output
    assert "gdrive" in result.output
    assert "imessage" in result.output
    assert "whatsapp" in result.output
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
    from jean_claude import auth, cli as cli_module, whatsapp
    from jean_claude.logging import JeanClaudeError

    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "nonexistent.json")
    monkeypatch.setattr(cli_module, "TOKEN_FILE", tmp_path / "nonexistent.json")

    # Mock WhatsApp binary as not available (may not be built in CI)
    def mock_get_path():
        raise JeanClaudeError("WhatsApp CLI not found")

    monkeypatch.setattr(whatsapp, "_get_whatsapp_cli_path", mock_get_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Not authenticated" in result.output


def test_completions_bash():
    """Test bash completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "bash"])
    assert result.exit_code == 0
    assert "_JEAN_CLAUDE_COMPLETE" in result.output
    assert "complete" in result.output.lower()


def test_completions_zsh():
    """Test zsh completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "jean-claude" in result.output


def test_completions_fish():
    """Test fish completions generation."""
    runner = CliRunner()
    result = runner.invoke(cli, ["completions", "fish"])
    assert result.exit_code == 0
    assert "_JEAN_CLAUDE_COMPLETE" in result.output


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


def test_command_reference_up_to_date(tmp_path):
    """Test that command reference files are up-to-date.

    This test generates command reference to a temp directory and compares
    it to the committed files. Fails if they differ, indicating someone
    forgot to run: uv run python scripts/generate-command-reference.py
    """
    from pathlib import Path
    import sys

    # Import the generation script
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root / "scripts"))
    from importlib import import_module

    generate_module = import_module("generate-command-reference")

    # Generate to temp directory
    generate_module.generate_reference(tmp_path)

    # Compare with existing files
    existing_dir = repo_root / "skills" / "jean-claude" / "commands"

    # Get all generated files
    generated_files = sorted(tmp_path.glob("*.txt"))
    existing_files = sorted(existing_dir.glob("*.txt"))

    # Check same number of files
    generated_names = {f.name for f in generated_files}
    existing_names = {f.name for f in existing_files}

    missing = generated_names - existing_names
    extra = existing_names - generated_names

    errors = []
    if missing:
        errors.append(f"Missing files in commands/: {missing}")
    if extra:
        errors.append(f"Extra files in commands/ (should be removed): {extra}")

    # Compare content of each file
    for gen_file in generated_files:
        existing_file = existing_dir / gen_file.name
        if existing_file.exists():
            gen_content = gen_file.read_text()
            exist_content = existing_file.read_text()
            if gen_content != exist_content:
                errors.append(f"Content mismatch: {gen_file.name}")

    if errors:
        error_msg = "\n".join(errors)
        raise AssertionError(
            f"Command reference files are out of date:\n{error_msg}\n\n"
            "Run: uv run python scripts/generate-command-reference.py"
        )
