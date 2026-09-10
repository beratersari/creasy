from __future__ import annotations

from creasy.gitlab.client import MergeRequest
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.review.findings import Finding
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
        return list(self.threads)

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
    assert gitlab.submit_calls == []


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
    assert gitlab.submit_calls == [(1, 2)]


def test_ask_job_does_not_open_finding_threads(tmp_config):
    gitlab = SpyGitlab()
    gitlab.discussions = []

    def post_discussion(project_id, mr_iid, body, position):
        gitlab.discussions.append({"body": body, "position": position})
        return {"id": "d"}

    gitlab.post_discussion = post_discussion  # type: ignore[method-assign]
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="ask",
        comment_text="Does this assume C++17?",
        text="Because dest is 8.",
        model="opencode/x",
    )
    result = RunResult(text=job.text, clone_path=".")
    finding = Finding(
        path="src/app.py",
        start_line=2,
        end_line=2,
        side="new",
        severity="critical",
        title="overflow",
        body="bad",
    )
    runner._post_note(job, result, findings=[finding])
    assert result.posted is True
    assert gitlab.notes
    assert gitlab.discussions == []


def test_ask_never_opens_finding_threads_even_if_text_says_review(tmp_config):
    gitlab = SpyGitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    posted: list[int] = []
    runner._post_discussions = lambda job, result, findings: posted.append(len(findings))
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="ask",
        comment_text="please do a new review",
        text="### Summary\nbad copy",
        model="opencode/x",
    )
    result = RunResult(text=job.text)
    finding = Finding(
        path="src/app.py",
        start_line=2,
        end_line=2,
        side="new",
        severity="critical",
        title="overflow",
        body="bad",
    )
    runner._post_note(job, result, findings=[finding])
    assert result.posted is True
    assert posted == []


def test_azure_loads_prior_comment_not_the_users_ask(tmp_config):
    gitlab = SpyGitlab()
    azure = SpyAzure()
    azure.threads = [
        {
            "id": 9,
            "comments": [
                {"id": 1, "content": "Unbounded strcpy into dest.", "parentCommentId": 0},
                {"id": 8, "content": "@creasy /ask why dest?", "parentCommentId": 1},
            ],
        }
    ]
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab, azure=azure)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-12",
        project_id=9,
        mr_iid=12,
        trigger="ask",
        provider="azure",
        azure_project=PROJECT,
        azure_repo=REPO,
        discussion_id="9",
        parent_comment_id=8,
        comment_text="why dest?",
    )
    runner._ensure_parent_comment(job)
    assert job.parent_comment_text == "Unbounded strcpy into dest."
    result = RunResult(text="Because dest is 8.")
    runner._post_note(job, result)
    assert azure.replies
    posted = azure.replies[0][1]
    assert "**Replying to**" not in posted
    assert "Unbounded strcpy into dest." not in posted
    assert "Because dest is 8." in posted


def test_ask_job_does_not_submit_gitlab_review(tmp_config):
    gitlab = SpyGitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="ask",
        text="Because the lock is per MR.",
        model="opencode/x",
    )
    result = RunResult(text=job.text)
    runner._post_note(job, result)
    assert result.posted is True
    assert gitlab.notes
    assert gitlab.submit_calls == []


def test_cancelled_or_error_note_does_not_submit_gitlab_review(tmp_config):
    gitlab = SpyGitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
    )
    cancelled = RunResult(cancelled=True)
    runner._post_note(job, cancelled)
    assert cancelled.posted is True
    assert gitlab.submit_calls == []

    failed = RunResult(error="opencode timed out")
    runner._post_note(job, failed)
    assert failed.posted is True
    assert gitlab.submit_calls == []


def test_submit_review_failure_does_not_fail_posted_job(tmp_config):
    gitlab = SpyGitlab()

    def boom(project_id: int, mr_iid: int) -> bool:
        raise RuntimeError("gitlab down")

    gitlab.submit_review = boom  # type: ignore[method-assign]
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="open",
        text="### Summary\nLooks fine.",
        model="opencode/x",
    )
    result = RunResult(text=job.text)
    runner._post_note(job, result)
    assert result.posted is True
    assert not result.error
    assert gitlab.notes
