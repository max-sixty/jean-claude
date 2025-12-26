"""Custom hatch build hook to set platform-specific wheel tags.

This hook ensures wheels are tagged for the platform they're built on,
since they contain platform-specific Go binaries.
"""

import platform
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface  # type: ignore[import-untyped]


def get_platform_tag() -> str:
    """Get the wheel platform tag for the current platform."""
    # Map Python platform info to wheel tag conventions
    os_name = sys.platform
    machine = platform.machine().lower()

    if os_name == "darwin":
        # macOS: macosx_11_0_arm64 or macosx_10_9_x86_64
        if machine == "arm64":
            return "macosx_11_0_arm64"
        else:
            return "macosx_10_9_x86_64"
    elif os_name == "linux":
        # Linux: manylinux2014 for broad compatibility
        if machine in ("x86_64", "amd64"):
            return "manylinux2014_x86_64"
        elif machine in ("arm64", "aarch64"):
            return "manylinux2014_aarch64"
        else:
            return f"linux_{machine}"
    elif os_name == "win32":
        if machine in ("amd64", "x86_64"):
            return "win_amd64"
        else:
            return "win32"
    else:
        # Fallback: use sysconfig
        import sysconfig

        return sysconfig.get_platform().replace("-", "_").replace(".", "_")


class CustomBuildHook(BuildHookInterface):
    """Build hook that sets platform-specific wheel tags."""

    def initialize(self, version: str, build_data: dict) -> None:
        """Set the wheel tag to be platform-specific."""
        platform_tag = get_platform_tag()
        # py3-none-{platform} means: Python 3, no ABI dependency, specific platform
        build_data["tag"] = f"py3-none-{platform_tag}"
