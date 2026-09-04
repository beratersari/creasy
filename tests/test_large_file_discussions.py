"""Deterministic large-file discussion mapping. No OpenCode, no GitLab."""

from __future__ import annotations

from creasy.gitlab.client import MergeRequest
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.review.findings import extract_markdown_findings
from creasy.workspace.gitops import clone_repo, fetch_and_checkout, resolve_merge_base, unified_diff
from creasy.workspace.identity import clone_path_for
from creasy.workspace.store import WorkspaceStore
from planted import init_planted_origin, review_markdown_for


class SpyGitlab:
    def __init__(self, mr: MergeRequest) -> None:
        self.mr = mr
        self.notes: list[str] = []
        self.discussions: list[dict] = []

    def get_merge_request(self, project_id: int, mr_iid: int) -> MergeRequest:
        return self.mr

    def resolve_http_url(self, project_id: int, fallback: str = "") -> str:
        return fallback or self.mr.http_url

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        self.notes.append(body)
        return {"ok": True}

    def post_discussion(self, project_id: int, mr_iid: int, body: str, position: dict) -> dict:
        self.discussions.append({"body": body, "position": position})
        return {"id": "d"}


def test_worker_posts_one_thread_per_planted_line_in_large_file(tmp_path, tmp_config):
    origin, sha, base, plants = init_planted_origin(tmp_path / "src")
    dest = clone_path_for(tmp_config.work_dir, "9-9")
    tmp_config.gitlab_token = ""
    clone_repo(origin.as_uri(), dest, token="", timeout=60)
    fetch_and_checkout(
        dest,
        source_branch="feat",
        target_branch="main",
        sha=sha,
        token="",
        timeout=60,
    )
    merge_base = resolve_merge_base(dest, target_branch="main", preferred_base=base)
    assert (dest / "src" / "big_planted.cpp").is_file()
    assert len((dest / "src" / "big_planted.cpp").read_text(encoding="utf-8").splitlines()) >= 1000
    assert plants[-1].line - plants[0].line >= 800

    mr = MergeRequest(
        project_id=9,
        iid=9,
        title="planted large file",
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
        log_file="planted.log",
    )
    result = RunResult(
        text=review_markdown_for(plants),
        clone_path=str(dest),
        merge_base=merge_base,
        sha=sha,
        base_sha=base,
        start_sha=base,
    )
    findings = extract_markdown_findings(result.text)
    assert {item.start_line for item in findings} == {plant.line for plant in plants}
    runner._post_note(job, result, findings=findings)
    assert result.posted
    assert spy.notes and "creasy-findings" not in spy.notes[0]
    lines = sorted(
        int(row["position"]["new_line"])
        for row in spy.discussions
        if row["position"].get("new_path") == "src/big_planted.cpp" and row["position"].get("new_line")
    )
    assert lines == sorted(plant.line for plant in plants)
    assert unified_diff(dest, merge_base).count("\n+") >= 1000
