"""Worker helper and mocked run() coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from creasy.azure.client import AzureError
from creasy.gitlab.client import MergeRequest
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.store import JobStore
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.opencode.serve import ServeHandle
from creasy.opencode.session import OpenCodeError
from creasy.review.findings import Finding
from creasy.workspace.gitops import DiffIndex, GitError
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore
from test_fixes import SpyGitlab, _job, _mr


def _runner(tmp_config, gitlab=None, azure=None, store=None):
    ws = WorkspaceStore(tmp_config.data_dir / "ws")
    st = store or JobStore(tmp_config.job_dir)
    return OpenCodeRunner(tmp_config, ws, gitlab or SpyGitlab(), store=st, azure=azure)


def test_worker_small_helpers(tmp_config, tmp_path):
    gitlab = SpyGitlab()
    store = JobStore(tmp_config.job_dir)
    runner = _runner(tmp_config, gitlab=gitlab, store=store)
    job = _job(log_file="job.log")
    runner._remember_title(job, "")
    runner._remember_title(job, job.mr_title)
    runner._remember_title(job, "New title")
    assert job.mr_title == "New title"
    runner.store = None
    runner._remember_title(job, "Other")
    runner.store = store
    runner.store.save = MagicMock(side_effect=OSError("no"))
    runner._remember_title(job, "Again")
    runner._persist_job(job, "x")
    runner._append_job_log(job, "hello")
    runner._append_job_log(_job(log_file=""), "skip")
    job.log_file = "job.log"
    runner._append_job_log(job, "line")
    job.provider = "gitlab"
    assert runner._bind_azure(job) is not None
    job.provider = "azure"
    runner.azure = None
    assert runner._bind_azure(job) is not None
    runner.azure = SimpleNamespace()
    assert runner._bind_azure(job) is not None
    class CM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    runner.azure = SimpleNamespace(bind=lambda *a, **k: CM())
    cm = runner._bind_azure(job)
    cm.__enter__()
    cm.__exit__(None, None, None)
    handle = ServeHandle(pid=9, port=1, base_url="http://x", proc=MagicMock(), log_path=tmp_path / "s.log")
    runner.store = None
    runner._record_spawn(job, handle)
    runner._track_git_pid(job, 0)
    runner._track_git_pid(job, 12)
    assert runner._git_kw(job, lambda: False)


def test_worker_load_prompt_and_usage(tmp_config):
    gitlab = SpyGitlab()
    azure = MagicMock()
    azure.get_pull_request.return_value = _mr()
    azure.bind = None
    runner = _runner(tmp_config, gitlab=gitlab, azure=azure)
    job = _job(trigger="reset")
    assert runner.run(job, lambda: False).text == ""
    job = _job(trigger="usage")
    gitlab.post_note = MagicMock(return_value={})
    out = runner.run(job, lambda: False)
    assert out.posted
    out = runner.run(_job(trigger="usage"), lambda: True)
    assert out.cancelled
    gitlab.post_note = MagicMock(side_effect=RuntimeError("no"))
    out = runner.run(_job(trigger="usage"), lambda: False)
    assert out.error
    job = _job(provider="azure", azure_project="p", azure_repo="r")
    runner.azure = None
    try:
        runner._load_change(job)
        raise AssertionError("expected")
    except AzureError:
        pass
    job.azure_project = ""
    runner.azure = azure
    try:
        runner._load_change(job)
        raise AssertionError("expected")
    except AzureError:
        pass
    job.azure_project = "p"
    azure.apply_collection = MagicMock()
    assert runner._load_change(job).title
    assert runner._load_change(_job()).title
    index = DiffIndex(merge_base="b", stat="a | 1", paths=["a.py"], statuses={})
    ws = WorkspaceRecord(mr_key="1-1", project_id=1, mr_iid=1, session_id="ses_1", last_sha="old")
    ask = _job(trigger="ask", comment_text="why?", comment_path="a.py", comment_start_line=1, comment_end_line=2)
    runner._prompt(ask, _mr(sha="new"), index, ws, created_new=True, previous_sha="old")
    thread = _job(trigger="review", discussion_id="d1", comment_text="focus", comment_path="a.py", comment_start_line=1)
    runner._prompt(thread, _mr(), index, ws, created_new=False)
    runner._prompt(_job(), _mr(), index, ws, created_new=False)


def test_worker_post_paths(tmp_config):
    gitlab = SpyGitlab()
    azure = MagicMock()
    azure.reply_to_thread.side_effect = RuntimeError("no")
    azure.post_overview.return_value = {}
    runner = _runner(tmp_config, gitlab=gitlab, azure=azure)
    job = _job(provider="azure", azure_project="p", azure_repo="r", discussion_id="9", parent_comment_id=2)
    assert runner._post_result_body(job, "hi") == "overview"
    azure.reply_to_thread.side_effect = None
    azure.reply_to_thread.return_value = {}
    assert runner._post_result_body(job, "hi") == "thread"
    runner.azure = None
    try:
        runner._post_result_body(job, "hi")
        raise AssertionError("expected")
    except AzureError:
        pass
    runner.azure = azure
    gitlab.reply_to_discussion = MagicMock(side_effect=RuntimeError("no"))
    gl_job = _job(discussion_id="d")
    assert runner._post_result_body(gl_job, "hi") == "overview"
    gitlab.reply_to_discussion = MagicMock(return_value={})
    assert runner._post_result_body(gl_job, "hi") == "thread"
    result = RunResult(posted=True)
    runner._post_note(_job(), result)
    result = RunResult(cancelled=True)
    runner._post_note(_job(), result)
    result = RunResult(error="boom")
    runner._post_note(_job(), result)
    result = RunResult(text="ok")
    runner._post_note(_job(trigger="ask"), result, findings=[Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")])
    gitlab.post_note = MagicMock(side_effect=RuntimeError("fail"))
    result = RunResult(text="ok")
    runner._post_note(_job(), result)
    runner._submit_gitlab_review(_job(provider="azure", azure_project="p", azure_repo="r"))
    runner._submit_gitlab_review(_job(trigger="ask"))
    gitlab.submit_review = None
    runner._submit_gitlab_review(_job())
    gitlab.submit_review = MagicMock(side_effect=RuntimeError("x"))
    runner._submit_gitlab_review(_job())
    gitlab.submit_review = MagicMock(return_value=False)
    runner._submit_gitlab_review(_job())
    gitlab.submit_review = MagicMock(return_value=True)
    runner._submit_gitlab_review(_job())


def test_worker_parent_and_threads(tmp_config):
    gitlab = SpyGitlab()
    gitlab.list_discussions = MagicMock(
        return_value=[
            {
                "id": "d1",
                "notes": [
                    {"id": 1, "body": "parent note"},
                    {"id": 2, "body": "child", "parent_comment_id": 1},
                ],
            }
        ]
    )
    runner = _runner(tmp_config, gitlab=gitlab)
    job = _job()
    runner._ensure_parent_comment(job)
    job.discussion_id = "d1"
    job.parent_comment_id = 2
    job.comment_text = "child"
    runner._ensure_parent_comment(job)
    assert job.parent_comment_text
    job.parent_comment_text = "already"
    runner._ensure_parent_comment(job)
    gitlab.list_discussions = MagicMock(side_effect=RuntimeError("x"))
    job = _job(discussion_id="d1", parent_comment_id=1)
    runner._ensure_parent_comment(job)
    assert runner._same_thread_id("", "1") is False
    assert runner._same_thread_id("1", "1")
    assert runner._same_thread_id("01", "1")
    assert runner._same_thread_id("a", "b") is False
    comments = [
        {"id": 1, "content": "first"},
        {"id": 2, "content": "second", "parentCommentId": 1},
        {"id": 3, "body": "third"},
    ]
    assert "first" in runner._prior_from_comments(comments, current_id=2, current_text="second")
    assert runner._prior_from_comments(comments, current_id=0, current_text="second")
    assert runner._prior_from_comments(comments, current_id=0, current_text="")
    azure = MagicMock()
    azure.list_threads.return_value = [
        {"id": "9", "comments": [{"id": 1, "content": "p"}, {"id": 2, "content": "c"}]},
        {"id": "8", "comments": [{"id": 9, "content": "other"}]},
    ]
    runner.azure = azure
    job = _job(provider="azure", azure_project="p", azure_repo="r", discussion_id="9", parent_comment_id=2, comment_text="c")
    assert runner._load_prior_thread_text(job)
    job.discussion_id = ""
    job.parent_comment_id = 2
    assert runner._load_prior_thread_text(job)
    runner.azure = None
    assert runner._load_prior_thread_text(job) == ""
    runner.azure = azure
    job = _job(discussion_id="")
    assert runner._load_prior_thread_text(job) == ""
    gitlab.list_discussions = MagicMock(return_value=[{"id": "nope", "notes": []}])
    job = _job(discussion_id="d1")
    assert runner._load_prior_thread_text(job) == ""


def test_worker_reply_and_existing(tmp_config):
    gitlab = SpyGitlab()
    azure = MagicMock()
    runner = _runner(tmp_config, gitlab=gitlab, azure=azure)
    job = _job(provider="azure", azure_project="p", azure_repo="r")
    azure.reply_to_thread.return_value = {}
    assert runner._reply_finding(job, "1", "b", parent_comment_id=2)
    azure.reply_to_thread.side_effect = RuntimeError("x")
    assert runner._reply_finding(job, "1", "b") is False
    runner.azure = None
    assert runner._reply_finding(job, "1", "b") is False
    runner.azure = azure
    gitlab.reply_to_discussion = MagicMock(return_value={})
    assert runner._reply_finding(_job(), "d", "b")
    gitlab.reply_to_discussion = MagicMock(side_effect=RuntimeError("x"))
    assert runner._reply_finding(_job(), "d", "b") is False
    gitlab.reply_to_discussion = None
    assert runner._reply_finding(_job(), "d", "b") is False
    azure.list_threads.return_value = []
    assert runner._existing_creasy_threads(job) == []
    azure.list_threads.side_effect = RuntimeError("x")
    assert runner._existing_creasy_threads(job) == []
    runner.azure = None
    assert runner._existing_creasy_threads(job) == []
    gitlab.list_discussions = MagicMock(side_effect=RuntimeError("x"))
    assert runner._existing_creasy_threads(_job()) == []
    gitlab.list_discussions = None
    assert runner._existing_creasy_threads(_job()) == []


def test_worker_post_discussions(tmp_config, tmp_path, monkeypatch):
    gitlab = SpyGitlab()
    gitlab.post_discussion = MagicMock(return_value={})
    runner = _runner(tmp_config, gitlab=gitlab)
    job = _job()
    result = RunResult(clone_path="", merge_base="")
    runner._post_discussions(job, result, [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")])
    clone = tmp_path / "ws" / "1-1"
    clone.mkdir(parents=True)
    result = RunResult(clone_path=str(clone), merge_base="abc", sha="def", base_sha="abc", start_sha="abc")
    monkeypatch.setattr("creasy.jobs.worker.unified_diff", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("diff")))
    runner._post_discussions(
        job,
        result,
        [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    monkeypatch.setattr(
        "creasy.jobs.worker.unified_diff",
        lambda *a, **k: "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-old\n+new\n",
    )
    monkeypatch.setattr("creasy.jobs.worker.parse_unified_diff", lambda text: __import__("creasy.workspace.diffmap", fromlist=["parse_unified_diff"]).parse_unified_diff(text))
    runner._post_discussions(
        job,
        result,
        [Finding(path="missing.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    gitlab.post_discussion = MagicMock(side_effect=RuntimeError("x"))
    runner._post_discussions(
        job,
        result,
        [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    azure = MagicMock()
    azure.iteration_span.return_value = (1, 2, "sha")
    azure.post_file_thread.return_value = {}
    azure.list_threads.return_value = []
    runner.azure = azure
    az_job = _job(provider="azure", azure_project="p", azure_repo="r")
    runner._post_discussions(
        az_job,
        result,
        [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    runner._post_discussions(
        az_job,
        result,
        [Finding(path="nope.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    gitlab.post_discussion = None
    runner._post_discussions(_job(), result, [])


def test_worker_run_mocked(tmp_config, tmp_path, monkeypatch):
    gitlab = SpyGitlab()
    runner = _runner(tmp_config, gitlab=gitlab)
    clone = tmp_path / "ws" / "1-1"
    (clone / ".git").mkdir(parents=True)
    rec = WorkspaceRecord(mr_key="1-1", project_id=1, mr_iid=1, clone_path=str(clone), last_sha="old", session_id="ses_old")
    runner.workspaces.save(rec)
    handle = ServeHandle(pid=11, port=22, base_url="http://127.0.0.1:22", proc=MagicMock(), log_path=tmp_path / "s.log")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def resume_or_create(self, inbound, title):
            return "ses_1", False

        def list_messages(self, sid):
            return [{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "### Summary\nok"}]}]

        def create_session(self, title):
            return "ses_new"

        def get_session(self, sid):
            return SimpleNamespace(status_code=200)

        def post_message(self, *a, **k):
            return None

        def wait_idle(self, *a, **k):
            return "### Summary\nok"

        def abort(self, sid):
            return None

        def close(self):
            return None

    monkeypatch.setattr("creasy.jobs.worker.start_serve", lambda **k: handle)
    monkeypatch.setattr("creasy.jobs.worker.stop_serve", lambda h: None)
    monkeypatch.setattr("creasy.jobs.worker.stop_job_holders", lambda *a, **k: None)
    monkeypatch.setattr("creasy.jobs.worker.OpenCodeClient", FakeClient)
    monkeypatch.setattr("creasy.jobs.worker.resolve_merge_base", lambda *a, **k: "base")
    monkeypatch.setattr("creasy.jobs.worker.diff_stat", lambda *a, **k: DiffIndex(merge_base="base", stat="a | 1", paths=["a.py"], statuses={}))
    monkeypatch.setattr(
        "creasy.jobs.worker.fetch_and_checkout",
        lambda *a, **k: "newsha",
    )
    monkeypatch.setattr("creasy.jobs.worker.clone_repo", lambda *a, **k: None)
    job = _job()
    out = runner.run(job, lambda: False)
    assert out.session_id == "ses_1"
    out = runner.run(_job(), lambda: True)
    assert out.cancelled

    class BoomGit:
        def get_merge_request(self, *a, **k):
            raise GitError("cancelled")

    runner.gitlab = BoomGit()
    out = runner.run(_job(), lambda: True)
    assert out.cancelled
    runner.gitlab = SpyGitlab()
    monkeypatch.setattr("creasy.jobs.worker.resolve_merge_base", lambda *a, **k: (_ for _ in ()).throw(GitError("bad git")))
    out = runner.run(_job(), lambda: False)
    assert "git" in out.error
    job = _job(provider="azure", azure_project="p", azure_repo="r")
    runner.azure = MagicMock()
    runner.azure.get_pull_request.side_effect = AzureError("no azure")
    runner.azure.bind = lambda *a, **k: __import__("contextlib").nullcontext()
    out = runner.run(job, lambda: False)
    assert out.error or out.cancelled

    class BoomBind:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            raise RuntimeError("unbind")

    runner.azure.bind = lambda *a, **k: BoomBind()
    runner.gitlab = SpyGitlab()
    monkeypatch.setattr("creasy.jobs.worker.resolve_merge_base", lambda *a, **k: "base")
    runner.run(_job(provider="azure", azure_project="p", azure_repo="r"), lambda: False)


def test_worker_run_retry_and_400(tmp_config, tmp_path, monkeypatch):
    gitlab = SpyGitlab()
    runner = _runner(tmp_config, gitlab=gitlab)
    tmp_config.opencode_retry_count = 2
    clone = tmp_path / "ws" / "1-1"
    (clone / ".git").mkdir(parents=True)
    handle = ServeHandle(pid=11, port=22, base_url="http://127.0.0.1:22", proc=MagicMock(), log_path=tmp_path / "s.log")
    state = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def resume_or_create(self, inbound, title):
            return "ses_1", True

        def list_messages(self, sid):
            if state["n"] == 0:
                state["n"] += 1
                raise OpenCodeError("messages unreadable", status_code=400)
            return [{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "### Summary\nok"}]}]

        def create_session(self, title):
            return "ses_new"

        def get_session(self, sid):
            return SimpleNamespace(status_code=200)

        def post_message(self, *a, **k):
            return None

        def wait_idle(self, *a, **k):
            if state["n"] < 3:
                state["n"] += 1
                raise OpenCodeError("hang")
            return "### Summary\nok"

        def abort(self, sid):
            raise RuntimeError("abort")

        def close(self):
            return None

    monkeypatch.setattr("creasy.jobs.worker.start_serve", lambda **k: handle)
    monkeypatch.setattr("creasy.jobs.worker.stop_serve", lambda h: None)
    monkeypatch.setattr("creasy.jobs.worker.stop_job_holders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr("creasy.jobs.worker.OpenCodeClient", FakeClient)
    monkeypatch.setattr("creasy.jobs.worker.resolve_merge_base", lambda *a, **k: "base")
    monkeypatch.setattr("creasy.jobs.worker.diff_stat", lambda *a, **k: DiffIndex(merge_base="base", stat="a | 1", paths=["a.py"], statuses={}))
    monkeypatch.setattr("creasy.jobs.worker.fetch_and_checkout", lambda *a, **k: "sha")
    job = _job(trigger="ask", comment_text="why?")
    out = runner.run(job, lambda: False)
    assert out.error or out.text

    class DeadClient(FakeClient):
        def list_messages(self, sid):
            raise OpenCodeError("dead", status_code=500)

    monkeypatch.setattr("creasy.jobs.worker.OpenCodeClient", DeadClient)
    try:
        runner.run(_job(), lambda: False)
    except Exception:
        pass

    class RetryClient(FakeClient):
        def list_messages(self, sid):
            return []

        def wait_idle(self, *a, **k):
            raise OpenCodeError("hang")

        def get_session(self, sid):
            return SimpleNamespace(status_code=404)

        def resume_or_create(self, inbound, title):
            return "not-ses", False

    monkeypatch.setattr("creasy.jobs.worker.OpenCodeClient", RetryClient)
    out = runner.run(_job(), lambda: False)
    assert out.error

    n = {"i": 0}

    class CancelClient(FakeClient):
        def list_messages(self, sid):
            return []

        def wait_idle(self, *a, **k):
            raise OpenCodeError("hang")

    monkeypatch.setattr("creasy.jobs.worker.OpenCodeClient", CancelClient)
    out = runner.run(_job(), lambda: (n.__setitem__("i", n["i"] + 1) or n["i"] > 2))
    assert out.cancelled or out.error
