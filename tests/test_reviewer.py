from __future__ import annotations

from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner
from creasy.workspace.store import WorkspaceStore


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


def test_usage_replies_on_request_thread(tmp_config) -> None:
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

        def get_merge_request(self, project_id: int, mr_iid: int):
            raise AssertionError("usage must not load the MR")

    gitlab = Gitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab)
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="usage",
        discussion_id="disc_9",
    )
    result = runner.run(job, lambda: False)
    assert result.posted is True
    assert gitlab.replies
    assert gitlab.replies[0][0] == "disc_9"
    assert "<!-- creasy-usage -->" in gitlab.replies[0][1]
    assert "/ask" in gitlab.replies[0][1]
    assert "/review" in gitlab.replies[0][1]
    assert gitlab.notes == []


def test_azure_usage_replies_on_request_thread(tmp_config) -> None:
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

        def get_pull_request(self, project: str, repo: str, pr_id: int):
            raise AssertionError("usage must not load the PR")

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
        trigger="usage",
        provider="azure",
        azure_project="App",
        azure_repo="repo",
        discussion_id="148",
        parent_comment_id=3,
    )
    result = runner.run(job, lambda: False)
    assert result.posted is True
    assert azure.replies == [("148", 3, result.text)]
    assert "<!-- creasy-usage -->" in result.text
    assert azure.overviews == []
