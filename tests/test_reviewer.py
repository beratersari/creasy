from __future__ import annotations

from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner
from creasy.workspace.store import WorkspaceStore


class _Gitlab:
    def __init__(self) -> None:
        self.added: list[tuple[int, int, int]] = []

    def current_user_id(self) -> int:
        return 99

    def add_reviewer(self, project_id: int, mr_iid: int, user_id: int) -> bool:
        self.added.append((project_id, mr_iid, user_id))
        return True


class _Azure:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, int, str]] = []

    def current_user_id(self) -> str:
        return "bot-guid"

    def add_reviewer(self, project: str, repo: str, pr_id: int, user_id: str) -> bool:
        self.added.append((project, repo, pr_id, user_id))
        return True


def test_ensure_reviewer_assigns_token_user(tmp_config) -> None:
    gitlab = _Gitlab()
    azure = _Azure()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab, azure=azure)
    gitlab_job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
        provider="gitlab",
    )
    runner._ensure_reviewer(gitlab_job)
    assert gitlab.added == [(1, 2, 99)]
    azure_job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-12",
        project_id=9,
        mr_iid=12,
        trigger="review",
        provider="azure",
        azure_project="App",
        azure_repo="repo",
    )
    runner._ensure_reviewer(azure_job)
    assert azure.added == [("App", "repo", 12, "bot-guid")]


def test_ensure_reviewer_failure_does_not_raise(tmp_config) -> None:
    class Boom:
        def current_user_id(self) -> int:
            return 1

        def add_reviewer(self, project_id: int, mr_iid: int, user_id: int) -> bool:
            raise RuntimeError("gitlab down")

    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), Boom())
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
    )
    runner._ensure_reviewer(job)


def test_result_replies_on_request_thread(tmp_config) -> None:
    class Gitlab:
        def __init__(self) -> None:
            self.notes: list[str] = []
            self.replies: list[tuple[str, str]] = []

        def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
            self.notes.append(body)
            return {"ok": True}

        def reply_to_discussion(self, project_id: int, mr_iid: int, discussion_id: str, body: str) -> dict:
            self.replies.append((discussion_id, body))
            return {"ok": True}

    gitlab = Gitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
        discussion_id="disc_9",
    )
    via = runner._post_result_body(job, "review body")
    assert via == "thread"
    assert gitlab.replies == [("disc_9", "review body")]
    assert gitlab.notes == []


def test_result_falls_back_to_overview_when_reply_fails(tmp_config) -> None:
    class Gitlab:
        def __init__(self) -> None:
            self.notes: list[str] = []

        def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
            self.notes.append(body)
            return {"ok": True}

        def reply_to_discussion(self, project_id: int, mr_iid: int, discussion_id: str, body: str) -> dict:
            raise RuntimeError("thread gone")

    gitlab = Gitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="review",
        discussion_id="disc_9",
    )
    via = runner._post_result_body(job, "review body")
    assert via == "overview"
    assert gitlab.notes == ["review body"]


def test_azure_result_replies_on_request_thread(tmp_config) -> None:
    class Azure:
        def __init__(self) -> None:
            self.overviews: list[str] = []
            self.replies: list[tuple[str, int, str]] = []

        def post_overview(self, project: str, repo: str, pr_id: int, body: str) -> dict:
            self.overviews.append(body)
            return {"ok": True}

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
            self.replies.append((thread_id, parent_comment_id, body))
            return {"ok": True}

    class Gitlab:
        def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
            raise AssertionError("gitlab must not post")

    azure = Azure()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), Gitlab(), azure=azure)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-12",
        project_id=9,
        mr_iid=12,
        trigger="review",
        provider="azure",
        azure_project="App",
        azure_repo="repo",
        discussion_id="148",
        parent_comment_id=3,
    )
    via = runner._post_result_body(job, "review body")
    assert via == "thread"
    assert azure.replies == [("148", 3, "review body")]
    assert azure.overviews == []


def test_usage_job_posts_help_without_opencode(tmp_config) -> None:
    class Gitlab:
        def __init__(self) -> None:
            self.notes: list[str] = []

        def current_user(self):
            return {"id": 99, "names": ["creasy"]}

        def current_user_id(self) -> int:
            return 99

        def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
            self.notes.append(body)
            return {"ok": True}

    gitlab = Gitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="usage",
    )
    result = runner.run(job, lambda: False)
    assert result.posted is True
    assert not result.error
    assert gitlab.notes
    assert "`@creasy /review`" in gitlab.notes[0]
    assert "`@creasy /ask why is this lock held?`" in gitlab.notes[0]
    assert "`@creasy /reset`" in gitlab.notes[0]
