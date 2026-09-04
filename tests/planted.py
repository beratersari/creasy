"""Shared planted C++ review bait for large-file tests."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Planted:
    path: str
    line: int
    kind: str
    needle: str


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def write_big_planted(path: Path) -> list[Planted]:
    """Write a 1000+ line file with four defects far apart. Returns located plants."""
    lines: list[str] = [
        "#include <cstring>",
        "#include <cstdio>",
        "#include <string>",
        "#include <string_view>",
        "#include <iostream>",
        "#include <vector>",
        "",
        "namespace planted {",
    ]

    def pad(target: int, tag: str) -> None:
        n = 0
        while len(lines) < target:
            n += 1
            lines.append(f"int {tag}_pad_{n:04d}(int x) {{ return x + {n % 13}; }}")

    pad(78, "a")
    lines.append("void copy_name(const char* src) {")
    lines.append("    char dest[8];")
    lines.append("    strcpy(dest, src);")
    lines.append("    puts(dest);")
    lines.append("}")
    pad(398, "b")
    lines.append("std::string_view temp_label() {")
    lines.append("    std::string s = \"temporary-label-that-dies\";")
    lines.append("    return s;")
    lines.append("}")
    pad(698, "c")
    lines.append("int* make_scratch() {")
    lines.append("    return new int[64];")
    lines.append("}")
    pad(998, "d")
    lines.append("int maybe_uninit(bool flag) {")
    lines.append("    int x;")
    lines.append("    if (flag) x = 1;")
    lines.append("    return x;")
    lines.append("}")
    pad(1095, "e")
    lines.append("}  // namespace planted")
    lines.append("")
    lines.append("int main() {")
    lines.append("    planted::copy_name(\"this-name-is-far-too-long-for-dest\");")
    lines.append("    std::cout << planted::temp_label() << '\\n';")
    lines.append("    (void)planted::make_scratch();")
    lines.append("    (void)planted::maybe_uninit(false);")
    lines.append("    return 0;")
    lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wanted = (
        ("overflow", "strcpy(dest, src);"),
        ("dangle", "return s;"),
        ("leak", "return new int[64];"),
        ("uninit", "return x;"),
    )
    found: list[Planted] = []
    rel = "src/big_planted.cpp"
    for kind, needle in wanted:
        for index, line in enumerate(lines, 1):
            if needle in line:
                found.append(Planted(path=rel, line=index, kind=kind, needle=needle))
                break
    if len(found) != 4:
        raise RuntimeError(f"expected 4 plants, got {found}")
    if len(lines) < 1000:
        raise RuntimeError(f"planted file too small: {len(lines)}")
    return found


def review_markdown_for(plants: list[Planted]) -> str:
    blocks = [
        "### Summary",
        f"{len(plants)} Critical. Do not merge.",
        "",
        "### Critical",
        "",
    ]
    for index, plant in enumerate(plants, 1):
        blocks.extend(
            [
                f"#### {index}. `{plant.path}:{plant.line}` — {plant.kind}",
                "",
                "**Code**",
                "```cpp",
                plant.needle,
                "```",
                "",
                "**Why it is an issue and where**",
                f"`{plant.path}:{plant.line}` — planted {plant.kind}.",
                "",
                "**Suggested fix**",
                "Do not do that.",
                "",
            ]
        )
    return "\n".join(blocks)


def init_planted_origin(root: Path) -> tuple[Path, str, str, list[Planted]]:
    """Bare-ish origin with main + feat. feat adds src/big_planted.cpp."""
    origin = root / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init")
    _git(origin, "checkout", "-B", "main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    (origin / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(planted)\n"
        "set(CMAKE_CXX_STANDARD 17)\n"
        "set(CMAKE_CXX_STANDARD_REQUIRED ON)\n"
        "add_executable(ok ok.cpp)\n",
        encoding="utf-8",
    )
    (origin / "ok.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (origin / "AGENTS.md").write_text("C++17 only.\n", encoding="utf-8")
    _git(origin, "add", "CMakeLists.txt", "ok.cpp", "AGENTS.md")
    _git(origin, "commit", "-m", "init")
    base = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-b", "feat")
    plants = write_big_planted(origin / "src" / "big_planted.cpp")
    cmake = (origin / "CMakeLists.txt").read_text(encoding="utf-8")
    (origin / "CMakeLists.txt").write_text(
        cmake + "add_executable(big_planted src/big_planted.cpp)\n",
        encoding="utf-8",
    )
    _git(origin, "add", "src/big_planted.cpp", "CMakeLists.txt")
    _git(origin, "commit", "-m", "add large planted file")
    sha = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "main")
    return origin, sha, base, plants
