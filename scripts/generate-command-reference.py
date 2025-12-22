#!/usr/bin/env python3
"""Generate command reference from --help output.

This script runs all jean-claude commands with --help and writes
the output to separate files in skills/jean-claude/commands/.
"""

import subprocess
from pathlib import Path


def get_help(command_parts: list[str]) -> str:
    """Run command with --help and return output."""
    cmd = ["uv", "run", "jean-claude"] + command_parts + ["--help"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def parse_commands(help_text: str) -> list[str]:
    """Extract command names from help output."""
    commands = []
    in_commands = False
    for line in help_text.split("\n"):
        if line.startswith("Commands:"):
            in_commands = True
            continue
        if in_commands:
            if not line.strip() or line[0] != " ":
                break
            # Extract command name (first word after leading spaces)
            parts = line.strip().split()
            if parts:
                commands.append(parts[0])
    return commands


def generate_reference(output_dir: Path) -> None:
    """Generate command reference files."""
    # Clean and recreate output directory
    if output_dir.exists():
        import shutil

        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    # Main help
    main_help = get_help([])
    (output_dir / "main.txt").write_text(main_help)
    print(f"Generated {output_dir / 'main.txt'}")

    # Top-level commands
    top_commands = parse_commands(main_help)

    for cmd in top_commands:
        cmd_help = get_help([cmd])

        # Write top-level command help
        cmd_file = output_dir / f"{cmd}.txt"
        cmd_file.write_text(cmd_help)
        print(f"Generated {cmd_file}")

        # Check for subcommands
        subcommands = parse_commands(cmd_help)

        for subcmd in subcommands:
            subcmd_help = get_help([cmd, subcmd])

            # Check for third-level commands (e.g., gmail draft create)
            third_commands = parse_commands(subcmd_help)

            if third_commands:
                # Command group (e.g., draft) - consolidate with third-level commands
                content = [f"# {cmd} {subcmd}", "", subcmd_help, ""]

                for third_cmd in third_commands:
                    third_help = get_help([cmd, subcmd, third_cmd])
                    content.extend(
                        [f"## {cmd} {subcmd} {third_cmd}", "", third_help, ""]
                    )

                subcmd_file = output_dir / f"{cmd}-{subcmd}.txt"
                subcmd_file.write_text("\n".join(content))
                print(f"Generated {subcmd_file}")
            else:
                # Simple subcommand - just write its help
                subcmd_file = output_dir / f"{cmd}-{subcmd}.txt"
                subcmd_file.write_text(subcmd_help)
                print(f"Generated {subcmd_file}")


if __name__ == "__main__":
    repo_root = Path(__file__).parent.parent
    output_dir = repo_root / "skills" / "jean-claude" / "commands"
    generate_reference(output_dir)
