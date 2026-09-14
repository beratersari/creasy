"""Operator executable zip suffixes. CI and release notes read this."""

from __future__ import annotations

WINDOWS_SUFFIX = "windows-x64"
DARWIN_SUFFIX = "darwin-arm64"
# linux-x64 is the Ubuntu 22.04 LTS build, kept for existing download scripts.
LINUX_ALIAS_SUFFIX = "linux-x64"
LINUX_ALIAS_UBUNTU = "22.04"
LINUX_UBUNTU_VERSIONS = ("18.04", "20.04", "22.04", "24.04")


def linux_ubuntu_suffix(version: str) -> str:
    return f"linux-ubuntu-{version}-x64"


def linux_suffixes() -> list[str]:
    return [linux_ubuntu_suffix(ver) for ver in LINUX_UBUNTU_VERSIONS] + [LINUX_ALIAS_SUFFIX]


def release_suffixes() -> list[str]:
    return [WINDOWS_SUFFIX, *linux_suffixes(), DARWIN_SUFFIX]


def drop_bundled_libz(dest_name: str) -> bool:
    """True when PyInstaller should omit this binary (use the OS copy)."""
    base = str(dest_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base == "libz.so.1" or base.startswith("libz.so.")
