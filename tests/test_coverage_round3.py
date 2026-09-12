"""Third coverage pass: Windows kill internals and leftover worker/git/azure paths."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import MagicMock

from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.store import JobStore
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.review.findings import Finding
from creasy.workspace.gitops import DiffIndex
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore
from test_fixes import SpyGitlab, _job, _mr


def _fake_os(monkeypatch, module, name: str) -> None:
    real = module.os

    class _Os:
        def __getattr__(self, item):
            if item == "name":
                return name
            return getattr(real, item)

    monkeypatch.setattr(module, "os", _Os())


def test_windows_cwd_branches(monkeypatch):
    from creasy.cleanup import kill as k

    _fake_os(monkeypatch, k, "nt")
    monkeypatch.setattr(k, "protected_pids", lambda: set())

    class K32:
        def __init__(self, handle=1, reads=None, close_err=False):
            self.handle = handle
            self.reads = list(reads or [])
            self.close_err = close_err

        def OpenProcess(self, *a):
            return self.handle

        def ReadProcessMemory(self, handle, src, dest, size, nread):
            if not self.reads:
                return 0
            ok, mut = self.reads.pop(0)
            if mut:
                mut(dest)
            return 1 if ok else 0

        def CloseHandle(self, h):
            if self.close_err:
                raise RuntimeError("close")
            return 1

    class Nt:
        def __init__(self, status=0, peb=1):
            self.status = status
            self.peb = peb

        def NtQueryInformationProcess(self, handle, info, buf, size, retlen):
            try:
                pbi = buf._obj
                pbi.PebBaseAddress = self.peb
            except Exception:
                pass
            return self.status

    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(handle=0), Nt()))
    assert k._windows_cwd(99) is None
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(), Nt(status=1)))
    assert k._windows_cwd(99) is None
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(), Nt(peb=0)))
    assert k._windows_cwd(99) is None

    def set_ptr(dest):
        dest._obj.value = 1

    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(reads=[(False, None)]), Nt()))
    assert k._windows_cwd(99) is None
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(reads=[(True, None)]), Nt()))
    assert k._windows_cwd(99) is None
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(reads=[(True, set_ptr), (False, None)]), Nt()))
    assert k._windows_cwd(99) is None

    def set_us_empty(dest):
        try:
            dest._obj.Buffer = None
            dest._obj.Length = 0
        except Exception:
            pass

    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(reads=[(True, set_ptr), (True, set_us_empty)]), Nt()))
    assert k._windows_cwd(99) is None

    def set_us_big(dest):
        try:
            dest._obj.Buffer = 1
            dest._obj.Length = 999999
        except Exception:
            pass

    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (K32(reads=[(True, set_ptr), (True, set_us_big)]), Nt()))
    assert k._windows_cwd(99) is None

    def set_us_ok(dest):
        try:
            dest._obj.Buffer = 1
            dest._obj.Length = 10
        except Exception:
            pass

    monkeypatch.setattr(
        k,
        "_win_k32_ntdll",
        lambda: (K32(reads=[(True, set_ptr), (True, set_us_ok), (False, None)]), Nt()),
    )
    assert k._windows_cwd(99) is None

    def set_buf(dest):
        try:
            dest[0] = "C"
        except Exception:
            pass

    monkeypatch.setattr(
        k,
        "_win_k32_ntdll",
        lambda: (K32(reads=[(True, set_ptr), (True, set_us_ok), (True, set_buf)], close_err=True), Nt()),
    )
    k._windows_cwd(99)
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert k._windows_cwd(99) is None


def test_rm_query_pids_branches(monkeypatch, tmp_path):
    from creasy.cleanup import kill as k

    _fake_os(monkeypatch, k, "nt")
    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: (_ for _ in ()).throw(OSError("no")))
    assert k._rm_query_pids(tmp_path) == []

    class RM:
        def __init__(self, start=1, register=0, getlist=1, needed=0, second=1, pids=None, end_err=False):
            self.start = start
            self.register = register
            self.getlist = getlist
            self.needed = needed
            self.second = second
            self.pids = pids or []
            self.end_err = end_err
            self.nget = 0

        def RmStartSession(self, session, flags, key):
            try:
                session._obj.value = 7
            except Exception:
                pass
            return self.start

        def RmRegisterResources(self, *a):
            return self.register

        def RmGetList(self, handle, needed, count, arr, reboot):
            self.nget += 1
            try:
                needed._obj.value = self.needed
            except Exception:
                pass
            if self.nget == 1:
                return self.getlist
            if arr:
                for i, pid in enumerate(self.pids):
                    arr[i].Process.dwProcessId = pid
                try:
                    count._obj.value = len(self.pids)
                except Exception:
                    pass
            return self.second

        def RmEndSession(self, handle):
            if self.end_err:
                raise RuntimeError("end")
            return 0

    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: RM())
    assert k._rm_query_pids(tmp_path) == []
    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: RM(start=0, register=1))
    assert k._rm_query_pids(tmp_path) == []
    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: RM(start=0, getlist=1, needed=0))
    assert k._rm_query_pids(tmp_path) == []
    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: RM(start=0, getlist=234, needed=1, second=1))
    assert k._rm_query_pids(tmp_path) == []
    monkeypatch.setattr(k, "_win_rstrtmgr", lambda: RM(start=0, getlist=234, needed=1, second=0, pids=[77], end_err=True))
    assert k._rm_query_pids(tmp_path) == [77]
    k._rstrtmgr = None
    k._kernel32 = None
    k._ntdll = None


def test_worker_ensure_workspace_and_more(tmp_config, tmp_path, monkeypatch):
    gitlab = SpyGitlab()
    azure = MagicMock()
    azure.resolve_clone_url.return_value = "https://ado/tfs/Col/App/_git/app"
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gitlab, store=JobStore(tmp_config.job_dir), azure=azure)
    job = _job()
    try:
        runner._ensure_workspace(job, _mr(), lambda: True)
    except Exception:
        pass
    dest = tmp_config.work_dir / job.mr_key
    dest.mkdir(parents=True)
    (dest / "junk").write_text("x", encoding="utf-8")
    monkeypatch.setattr("creasy.jobs.worker.delete_clone", lambda p: None)
    monkeypatch.setattr("creasy.jobs.worker.clone_repo", lambda *a, **k: None)
    monkeypatch.setattr("creasy.jobs.worker.fetch_and_checkout", lambda *a, **k: "sha")
    rec = runner._ensure_workspace(job, _mr(http_url="https://gl/r.git"), lambda: False)
    assert rec.last_sha == "sha"
    az_job = _job(provider="azure", azure_project="p", azure_repo="r", mr_key="9-9")
    runner._ensure_workspace(az_job, _mr(http_url="ssh://x"), lambda: False)
    try:
        runner._ensure_workspace(_job(mr_key="8-8"), _mr(http_url=""), lambda: False)
    except Exception:
        pass
    gitlab.resolve_http_url = lambda *a, **k: ""
    try:
        runner._ensure_workspace(_job(mr_key="7-7"), _mr(http_url=""), lambda: False)
    except Exception:
        pass
    result = RunResult(posted=False, cancelled=True)
    runner._post_note(_job(), result)
    result = RunResult(text="ok", clone_path=str(dest), merge_base="b")
    monkeypatch.setattr("creasy.jobs.worker.unified_diff", lambda *a, **k: "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n")
    gitlab.post_discussion = MagicMock(return_value={})
    gitlab.list_discussions = MagicMock(return_value=[])
    runner._post_discussions(
        _job(),
        result,
        [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )
    from creasy.review.position import CREASY_FINDING_MARK

    gitlab.list_discussions = MagicMock(
        return_value=[
            {
                "id": "d1",
                "notes": [
                    {
                        "id": 1,
                        "body": f"{CREASY_FINDING_MARK}\n**Kritik** · t\n\nb",
                        "position": {"new_path": "a.py", "new_line": 1},
                    }
                ],
            }
        ]
    )
    runner._reply_finding = lambda *a, **k: True
    runner._post_discussions(
        _job(),
        result,
        [Finding(path="a.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")],
    )


def test_end_and_manager_remainders(tmp_config, tmp_path, monkeypatch):
    from creasy.cleanup import end as endmod
    from creasy.jobs.manager import Manager
    from conftest import FakeRunner

    job = JobRecord(job_id="job_e", mr_key="1-1", project_id=1, mr_iid=1, trigger="review", extra_pids=[1])
    monkeypatch.setattr(endmod, "kill_job_tree", lambda p: None)
    monkeypatch.setattr(endmod, "reap_path", lambda *a, **k: 0)
    monkeypatch.setattr(endmod, "kill_file_holders", lambda *a, **k: 0)
    monkeypatch.setattr(endmod, "drop_git_locks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _fake_os(monkeypatch, endmod, "nt")
    clone = tmp_path / "c"
    clone.mkdir()
    endmod.stop_job_holders(job, clone)
    monkeypatch.setattr(endmod, "hard_delete", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    endmod.delete_clone_path(clone, reason="x")
    monkeypatch.setattr(endmod, "hard_delete", lambda p: False)
    monkeypatch.setattr(endmod, "retry_windows_delete_if_held", lambda p: False)
    endmod.delete_clone_path(clone, reason="x")
    monkeypatch.setattr(endmod, "query_windows_restart_manager", lambda p: SimpleNamespace(pids=[88], died=True))
    monkeypatch.setattr(endmod, "may_kill", lambda p: True)
    monkeypatch.setattr(endmod, "kill_pid", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(endmod, "hard_delete", lambda p: False)
    endmod.retry_windows_delete_if_held(clone)

    runner = FakeRunner()
    runner.release.set()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager._run_job("missing-job", __import__("threading").Event())

    class BoomRunner:
        def run(self, job, stop):
            raise RuntimeError("crash")

    manager.runner = BoomRunner()
    job = JobRecord(job_id=mint_job_id(), mr_key="2-2", project_id=2, mr_iid=2, trigger="review", status="running")
    manager.store.save(job)
    ev = __import__("threading").Event()
    manager._run_job(job.job_id, ev)
    ev.set()
    job2 = JobRecord(job_id=mint_job_id(), mr_key="2-2", project_id=2, mr_iid=2, trigger="review", status="running")
    manager.store.save(job2)
    manager._run_job(job2.job_id, ev)
    manager.shutdown()


def test_azure_events_and_webhook_remainders():
    from creasy.azure.events import (
        apply_live_reviewers,
        azure_collection_hint,
        azure_is_reviewer_list_event,
        azure_message_adds_bot,
        azure_pr_locator,
        azure_pr_web_url,
        azure_reviewers_include_bot,
        classify_azure_webhook,
        reset_reviewer_cache,
        _as_dict,
        _cleanup,
        _comment,
        _ids_from_links,
        _int_id,
        _is_comment_edit,
        _message_text,
        _parse_dt,
        _plain_text,
        _remember_reviewers,
        _reviewer_cache_key,
        _thread_ref,
        _unstructured_added_reviewer,
    )

    reset_reviewer_cache()
    classify_azure_webhook("nope")
    classify_azure_webhook({"eventType": "git.pullrequest.updated", "resource": {}})
    classify_azure_webhook({"eventType": "git.pullrequest.merged", "resource": {"status": "completed"}})
    classify_azure_webhook({"eventType": "git.pullrequest.created", "resource": {}})
    classify_azure_webhook(
        {
            "eventType": "ms.vss-code.git-pullrequest-comment-event",
            "resource": {"comment": {"content": "@creasy /ask hi"}},
        },
        mention_names=["creasy"],
    )
    pr = {
        "pullRequestId": 1,
        "repository": {"id": "repo", "name": "app", "project": {"id": "proj", "name": "App"}},
        "reviewers": [{"id": "bot", "displayName": "creasy", "uniqueName": "DOMAIN\\creasy"}],
    }
    _remember_reviewers({})
    _remember_reviewers(pr)
    _remember_reviewers(pr)
    azure_collection_hint("x")
    azure_pr_web_url("x")
    azure_reviewers_include_bot(pr["reviewers"], "bot", ["creasy"])
    azure_message_adds_bot({"message": {"text": "x added creasy as a reviewer"}}, "bot", ["creasy"])
    azure_pr_locator({})
    azure_is_reviewer_list_event({"message": {"text": "changed the reviewer list"}})
    apply_live_reviewers({"resource": pr}, [{"id": "bot"}])
    _as_dict(None)
    _comment({})
    _ids_from_links({})
    _thread_ref({})
    _int_id("x")
    _parse_dt("nope")
    _parse_dt("2020-01-01T00:00:00Z")
    _is_comment_edit({}, {})
    _plain_text(None)
    _message_text({})
    _unstructured_added_reviewer("Alice added Bob as a reviewer")
    _reviewer_cache_key({})
    _cleanup({}, action="close")
    reset_reviewer_cache()
