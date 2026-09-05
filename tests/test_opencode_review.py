"""Live OpenCode review. Skipped unless CREASY_LIVE_OPENCODE=1.

Default pytest stays offline (no opencode binary, no model, no GitLab).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from creasy.gitlab.client import MergeRequest
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner
from creasy.workspace.store import WorkspaceStore
from planted import init_planted_origin


def _opencode_bin() -> str | None:
    explicit = (os.getenv("OPENCODE_BIN") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("opencode")
    if found:
        return found
    home = Path.home() / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    if home.is_file():
        return str(home)
    return None


LIVE = os.getenv("CREASY_LIVE_OPENCODE", "").strip() in {"1", "true", "yes", "on"}
OPENCODE = _opencode_bin()

pytestmark = [
    pytest.mark.live_opencode,
    pytest.mark.skipif(
        not LIVE or not OPENCODE,
        reason="set CREASY_LIVE_OPENCODE=1 and install opencode to run",
    ),
]


class SpyGitlab:
    def __init__(self, mr: MergeRequest) -> None:
        self.mr = mr
        self.notes: list[str] = []
        self.discussions: list[dict] = []
        self.replies: list[dict] = []
        self._threads: list[dict] = []

    def get_merge_request(self, project_id: int, mr_iid: int) -> MergeRequest:
        return self.mr

    def resolve_http_url(self, project_id: int, fallback: str = "") -> str:
        return fallback or self.mr.http_url

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        self.notes.append(body)
        return {"ok": True}

    def list_discussions(self, project_id: int, mr_iid: int) -> list:
        return list(self._threads)

    def reply_to_discussion(self, project_id: int, mr_iid: int, discussion_id: str, body: str) -> dict:
        self.replies.append({"id": discussion_id, "body": body})
        for thread in self._threads:
            if thread["id"] == discussion_id:
                thread["notes"].append({"body": body, "resolved": False})
                break
        return {"id": discussion_id}

    def post_discussion(self, project_id: int, mr_iid: int, body: str, position: dict) -> dict:
        disc_id = f"d{len(self._threads) + 1}"
        row = {"id": disc_id, "body": body, "position": position}
        self.discussions.append(row)
        self._threads.append(
            {
                "id": disc_id,
                "notes": [{"body": body, "resolved": False, "position": position}],
            }
        )
        return {"id": disc_id}


def _span(position: dict) -> tuple[str, int, int]:
    path = str(position.get("new_path") or position.get("old_path") or "")
    line_range = position.get("line_range") or {}
    start = line_range.get("start") if isinstance(line_range.get("start"), dict) else {}
    end = line_range.get("end") if isinstance(line_range.get("end"), dict) else {}
    lo = int(start.get("new_line") or position.get("new_line") or 0)
    hi = int(end.get("new_line") or lo)
    if lo > hi:
        lo, hi = hi, lo
    return path, lo, hi


def _overlaps(left: tuple[str, int, int], right: tuple[str, int, int]) -> bool:
    return left[0] == right[0] and left[0] != "" and left[1] <= right[2] and right[1] <= left[2]


def test_opencode_review_threads_large_file(tmp_path, tmp_config, monkeypatch):
    origin, sha, base, plants = init_planted_origin(tmp_path / "src")
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = OPENCODE or "opencode"
    tmp_config.opencode_model = (os.getenv("OPENCODE_MODEL") or tmp_config.opencode_model).strip()
    tmp_config.opencode_timeout = 900
    tmp_config.hang_timeout = 300
    tmp_config.opencode_retry_count = 1
    tmp_config.serve_health_timeout = 90
    bin_dir = str(Path(tmp_config.opencode_bin).parent)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))

    mr = MergeRequest(
        project_id=9,
        iid=9,
        title="planted large C++ file",
        description="",
        author="t",
        source_branch="feat",
        target_branch="main",
        sha=sha,
        base_sha=base,
        start_sha=base,
        web_url="http://gl/mr/9",
        http_url=origin.as_uri(),
        draft=False,
        state="opened",
    )
    spy = SpyGitlab(mr)
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), spy)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-9",
        project_id=9,
        mr_iid=9,
        trigger="review",
        log_file="live-opencode.log",
        source_branch="feat",
        target_branch="main",
        sha=sha,
    )
    result = runner.run(job, lambda: False)
    assert result.error == "", result.error
    assert result.posted
    assert result.cancelled is False
    assert "src/big_planted.cpp" in (result.diff_stat or "")
    assert spy.notes
    note = spy.notes[0]
    assert "opencoderman-findings" not in note
    assert first_command_safe(note) is None

    planted_threads = [
        row
        for row in spy.discussions
        if (row["position"].get("new_path") == "src/big_planted.cpp" and row["position"].get("new_line"))
    ]
    lines = sorted({int(row["position"]["new_line"]) for row in planted_threads})
    assert len(lines) >= 2, f"expected multiple line threads, got {lines} note={note[:400]}"
    planted_lines = {plant.line for plant in plants}
    assert any(line in planted_lines or any(abs(line - p) <= 3 for p in planted_lines) for line in lines), (
        f"threads {lines} missed planted {sorted(planted_lines)}"
    )
    blob = note.lower() + "\n".join(row["body"].lower() for row in planted_threads)
    assert "strcpy" in blob or "overflow" in blob or "dangl" in blob or "uninit" in blob or "leak" in blob


def test_opencode_second_review_replies_on_overlap(tmp_path, tmp_config, monkeypatch):
    origin, sha, base, plants = init_planted_origin(tmp_path / "src")
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = OPENCODE or "opencode"
    tmp_config.opencode_model = (os.getenv("OPENCODE_MODEL") or tmp_config.opencode_model).strip()
    tmp_config.opencode_timeout = 900
    tmp_config.hang_timeout = 300
    tmp_config.opencode_retry_count = 1
    tmp_config.serve_health_timeout = 90
    bin_dir = str(Path(tmp_config.opencode_bin).parent)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))

    mr = MergeRequest(
        project_id=9,
        iid=9,
        title="planted large C++ file",
        description="",
        author="t",
        source_branch="feat",
        target_branch="main",
        sha=sha,
        base_sha=base,
        start_sha=base,
        web_url="http://gl/mr/9",
        http_url=origin.as_uri(),
        draft=False,
        state="opened",
    )
    spy = SpyGitlab(mr)
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)

    def _job() -> JobRecord:
        return JobRecord(
            job_id=mint_job_id(),
            mr_key="9-9",
            project_id=9,
            mr_iid=9,
            trigger="review",
            log_file=f"live-{mint_job_id()}.log",
            source_branch="feat",
            target_branch="main",
            sha=sha,
        )

    first = runner.run(_job(), lambda: False)
    assert first.error == "", first.error
    assert first.posted
    assert spy.discussions, "first review posted no diff threads"
    first_spans = [_span(row["position"]) for row in spy.discussions]
    first_count = len(spy.discussions)

    second = runner.run(_job(), lambda: False)
    assert second.error == "", second.error
    assert second.posted
    later = spy.discussions[first_count:]
    for row in later:
        later_span = _span(row["position"])
        assert not any(_overlaps(later_span, old) for old in first_spans), (
            f"second review opened a new thread on an existing range {later_span} "
            f"first={first_spans} replies={spy.replies}"
        )
    if second.findings_posted:
        assert spy.replies, (
            f"second review posted {second.findings_posted} finding(s) but replied to none; "
            f"new={later} first={first_spans}"
        )


def first_command_safe(body: str):
    from creasy.gitlab.events import first_command

    return first_command(body)
