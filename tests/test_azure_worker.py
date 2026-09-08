from __future__ import annotations

from creasy.gitlab.client import MergeRequest
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.workspace.store import WorkspaceStore
from test_azure_events import PROJECT, REPO
from test_fixes import SpyGitlab


class SpyAzure:
    def __init__(self) -> None:
        self.overviews: list[str] = []
        self.threads: list[dict] = []
        self.replies: list[tuple[str, str]] = []

    def current_user_id(self) -> str:
        return "azure-bot"

    def get_pull_request(self, project: str, repo: str, pr_id: int) -> MergeRequest:
        return MergeRequest(
            project_id=0,
            iid=pr_id,
            title="Add overflow",
            description="",
            author="dev",
            source_branch="feat",
            target_branch="main",
            sha="abc",
            base_sha="",
            start_sha="",
            web_url="http://ado/pr/12",
            http_url="https://ado.example/tfs/DefaultCollection/App/_git/app",
            draft=False,
            state="active",
        )

    def post_overview(self, project: str, repo: str, pr_id: int, body: str) -> dict:
        self.overviews.append(body)
        return {"id": 1}

    def post_file_thread(self, project: str, repo: str, pr_id: int, body: str, ctx: dict) -> dict:
        self.threads.append({"body": body, "ctx": ctx})
        return {"id": 2}

    def list_threads(self, project: str, repo: str, pr_id: int) -> list:
        return []

    def reply_to_thread(
        self,
        project: str,
        repo: str,
        pr_id: int,
        thread_id: str,
        body: str,
        *,
        parent_comment_id: int = 0,
    ) -> dict:
        self.replies.append((thread_id, body, parent_comment_id))
        return {"id": 3}

    def resolve_clone_url(self, project: str, repo: str, fallback: str = "") -> str:
        return fallback or "https://ado.example/tfs/DefaultCollection/App/_git/app"

    def iteration_span(self, project: str, repo: str, pr_id: int) -> tuple[int, int, str]:
        return 1, 2, "abc"


def test_azure_job_posts_overview_to_azure_not_gitlab(tmp_config):
    gitlab = SpyGitlab()
    azure = SpyAzure()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab, azure=azure)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-12",
        project_id=9,
        mr_iid=12,
        trigger="review",
        provider="azure",
        azure_project=PROJECT,
        azure_repo=REPO,
        text="### Summary\nLooks risky.",
        model="opencode/x",
    )
    result = RunResult(text=job.text)
    runner._post_note(job, result)
    assert result.posted is True
    assert azure.overviews
    assert "Looks risky" in azure.overviews[0]
    assert gitlab.notes == []


def test_gitlab_job_still_posts_to_gitlab(tmp_config):
    gitlab = SpyGitlab()
    azure = SpyAzure()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab, azure=azure)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
        text="### Summary\nLooks fine.",
        model="opencode/x",
    )
    result = RunResult(text=job.text)
    runner._post_note(job, result)
    assert result.posted is True
    assert gitlab.notes
    assert azure.overviews == []