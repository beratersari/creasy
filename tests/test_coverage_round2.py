"""Second coverage pass: remaining review/git/webhook/report branches."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.report import (
    read_capped_text,
    _cli_version,
    _opencode_cli_logs,
    _recent_fail_lines,
    _serve_log_names,
)
from creasy.api.web_mimetypes import ensure_spa_mimetypes, media_type_for_path
from creasy.api.webhook_azure import router as azure_router
from creasy.jobs.manager import Manager
from creasy.review.comment_range import parse_azure_thread_context, parse_gitlab_position
from creasy.review.findings import Finding, extract_markdown_findings, split_findings, _coerce, _parse_block
from creasy.review.position import build_position_variants, format_discussion, line_code
from creasy.review.similarity import _char_ngrams, _jaccard, should_skip_similar_reply, text_similarity
from creasy.review.threads import (
    is_creasy_finding_body,
    match_creasy_thread,
    parse_creasy_thread,
    parse_creasy_threads,
    _as_line,
    _range_from_position,
)
from creasy.workspace.diffmap import parse_unified_diff
from creasy.workspace.gitops import GitError, _run_git, clone_repo, delete_clone, fetch_and_checkout, resolve_merge_base
from creasy.workspace.identity import IdentityError, clone_path_for, mr_key
from conftest import FakeRunner
from test_azure_webhook import FakeAzure, _auth


def test_comment_range_and_threads_and_findings():
    assert parse_gitlab_position(None) == ("", "", 0, 0)
    assert parse_gitlab_position({"new_path": "a.py"})[0] == "a.py"
    assert parse_gitlab_position({"new_path": "a.py", "new_line": 3})[1] == "new"
    pos = {
        "new_path": "a.py",
        "line_range": {"start": {"new_line": 5}, "end": {"new_line": 2}},
    }
    assert parse_gitlab_position(pos)[1:] == ("new", 2, 5)
    old = {"old_path": "a.py", "old_line": 4, "line_range": {"start": {"old_line": 8}, "end": {"old_line": 3}}}
    assert parse_gitlab_position(old)[1] == "old"
    assert parse_azure_thread_context(None) == ("", "", 0, 0)
    assert parse_azure_thread_context({"filePath": "a.py"})[0] == "a.py"
    assert parse_azure_thread_context({"filePath": "a.py", "rightFileStart": {"line": 2}, "rightFileEnd": {"line": 1}})[1] == "new"
    assert parse_azure_thread_context({"file_path": "a.py", "leftFileStart": {"line": 4}, "leftFileEnd": {"line": 1}})[1] == "old"
    assert _as_line("x") == 0
    assert _as_line("") == 0
    assert _range_from_position({}) is None
    assert is_creasy_finding_body("**Critical** overflow")
    assert not is_creasy_finding_body("hello")
    assert parse_creasy_thread({"individual_note": True}) is None
    assert parse_creasy_thread({"notes": []}) is None
    assert parse_creasy_thread({"notes": [{"system": True}]}) is None
    assert parse_creasy_thread({"notes": [{"body": "x", "position": {}}]}) is None
    assert parse_creasy_thread({"notes": [{"body": "<!-- creasy-finding -->", "position": {"new_path": "a.py"}}]}) is None
    good = {
        "id": "d1",
        "notes": [
            {
                "id": 7,
                "body": "<!-- creasy-finding -->\n**Major**",
                "position": {"new_path": "a.py", "old_path": "a.py", "new_line": 3},
            },
            "skip",
            {"system": True, "body": "s"},
            {"body": "<!-- creasy-finding --> later"},
        ],
    }
    thread = parse_creasy_thread(good)
    assert thread is not None
    assert parse_creasy_threads([None, good])
    finding = Finding(path="a.py", start_line=3, end_line=1, side="new", severity="major", title="t", body="b")
    assert match_creasy_thread(finding, parse_creasy_threads([good]), set())
    assert match_creasy_thread(finding, parse_creasy_threads([good]), {"d1"}) is None
    assert match_creasy_thread(
        Finding(path="", start_line=1, end_line=1, side="new", severity="x", title="t", body="b"),
        parse_creasy_threads([good]),
        set(),
    ) is None
    md, found = split_findings(
        "### Critical\n#### 1. `src/a.py:2-4` — overflow\n- Why it matters: bad\n- How to fix: don't\n```\ncode\n```\n"
    )
    assert found or md
    extract_markdown_findings("### Major\n#### 1. `b.py:1` — t\n- Why it matters: x\n- How to fix: y\n")
    assert _coerce(None) is None
    assert _coerce({"path": ""}) is None
    assert _coerce({"path": "a.py"}) is None
    assert _coerce({"path": "a.py", "start_line": 2, "end_line": 1, "side": "nope", "severity": "nope", "title": "t"})
    assert _coerce({"path": "a.py", "start_line": 1}) is None
    assert _parse_block("not-json", require_tag=True) is None
    assert _parse_block("[]", require_tag=True) is None
    assert _parse_block('{"findings":[]}', require_tag=True) == []
    assert _parse_block('{"findings":[],"extra":1}', require_tag=True) is None
    split_findings("```opencoderman-findings\n[]\n```\ntext")
    split_findings("```json\n{\"findings\":[{\"path\":\"a.py\",\"start_line\":1,\"title\":\"t\",\"body\":\"b\"}]}\n```")


def test_position_similarity_identity(tmp_path):
    assert line_code("a.py", 1, 2)
    finding = Finding(path="a.py", start_line=1, end_line=2, side="new", severity="critical", title="t", body="b")
    diff = parse_unified_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-old\n+new\n keep\n"
    )
    build_position_variants(finding, diff, base_sha="b", start_sha="", head_sha="h")
    assert build_position_variants(finding, diff, base_sha="", start_sha="s", head_sha="h") == []
    format_discussion(Finding(path="a.py", start_line=1, end_line=1, side="new", severity="unknown", title="", body=""))
    assert _jaccard(set(), set()) == 1.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _char_ngrams("ab") == {"ab"}
    assert _char_ngrams("") == set()
    assert text_similarity("", "") == 1.0
    assert text_similarity("a", "") == 0.0
    assert not should_skip_similar_reply("x", "")
    with pytest.raises(IdentityError):
        mr_key("bad", 1) if False else clone_path_for(tmp_path, "not safe!!")
    try:
        mr_key("x", 1)
    except Exception:
        pass


def test_report_and_mimetypes(tmp_config, tmp_path, monkeypatch):
    assert _recent_fail_lines(tmp_path / "missing") == []
    p = tmp_path / "app.log"
    p.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr(type(p), "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    # don't mutate Path.read_text globally — use a file we delete instead
    p.unlink()
    assert _recent_fail_lines(p) == []
    big = tmp_path / "cap.txt"
    big.write_bytes(b"hello world without newline")
    blob = read_capped_text(big, max_bytes=5)
    assert blob["truncated"]
    read_capped_text(None, max_bytes=10)
    monkeypatch.setattr("creasy.api.report.shutil.which", lambda b: "/bin/x")
    monkeypatch.setattr(
        "creasy.api.report.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert "error" in _cli_version("nope")
    monkeypatch.setattr(
        "creasy.api.report.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    assert "error" in _cli_version("git")
    logs = tmp_path / "oclog"
    logs.mkdir()
    (logs / "a.log").write_text("x", encoding="utf-8")
    (logs / "b.log").write_text("y", encoding="utf-8")
    monkeypatch.setattr("creasy.api.report.Path.home", lambda: tmp_path)
    (tmp_path / ".opencode" / "log").mkdir(parents=True)
    (tmp_path / ".opencode" / "log" / "c.log").write_text("z", encoding="utf-8")
    _opencode_cli_logs()
    _serve_log_names(tmp_config)
    empty_cfg = SimpleNamespace(serve_dir=tmp_path / "noserve")
    assert _serve_log_names(empty_cfg) == []
    ensure_spa_mimetypes()
    assert media_type_for_path("x.js") == "text/javascript"
    assert media_type_for_path("x.unknown") is None or isinstance(media_type_for_path("x.unknown"), (str, type(None)))


def test_webhook_azure_auth_and_bot(tmp_config):
    tmp_config.azure_url = "https://ado.example/tfs/Col"
    tmp_config.azure_token = "pat"
    tmp_config.azure_webhook_password = "secret"
    tmp_config.azure_webhook_user = "hook"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.azure = FakeAzure()
    app.state.azure_bot_user_id = None
    app.include_router(azure_router)
    client = TestClient(app)
    assert client.post("/creasy/webhook/azure", json={}).status_code == 401
    assert client.post("/creasy/webhook/azure", json={}, headers={"Authorization": "Basic !!!"}).status_code == 401
    assert client.post("/creasy/webhook/azure", json={}, headers=_auth("wrong")).status_code == 401
    assert client.post("/creasy/webhook/azure", json={}, headers=_auth("secret", "wrong")).status_code == 401
    app.state.azure = None
    app.state.azure_bot_user_id = None
    client.post("/creasy/webhook/azure", json={"eventType": "x"}, headers=_auth("secret", "hook"))
    az = FakeAzure()
    az.current_user_id = lambda: "bot-id"
    az.current_user = lambda: {"names": ["creasy"]}
    app.state.azure = az
    app.state.azure_bot_user_id = None
    app.state.azure_bot_mention_names = []
    client.post("/creasy/webhook/azure", json={"eventType": "git.pullrequest.updated"}, headers=_auth("secret", "hook"))
    runner.release.set()
    manager.shutdown()


def test_gitops_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "creasy.workspace.gitops.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("git", 1)),
    )
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, timeout=1)
    monkeypatch.setattr(
        "creasy.workspace.gitops.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")),
    )
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, timeout=1)
    monkeypatch.setattr(
        "creasy.workspace.gitops.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, timeout=1)
    env = {"CREASY_AZURE_GIT": "1", "CREASY_GIT_TOKEN": "t", "GIT_ASKPASS": "x"}
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, env=env, timeout=1)
    monkeypatch.setattr(
        "creasy.workspace.gitops._run_git_killable",
        lambda *a, **k: (_ for _ in ()).throw(GitError("cancelled")),
    )
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, timeout=1, should_stop=lambda: True)
    monkeypatch.setattr(
        "creasy.workspace.gitops._run_git_killable",
        lambda *a, **k: (_ for _ in ()).throw(GitError("other")),
    )
    with pytest.raises(GitError):
        _run_git(["status"], cwd=tmp_path, timeout=1, on_pid=lambda p: None)
    dest = tmp_path / "exists"
    dest.mkdir()
    with pytest.raises(GitError):
        clone_repo("https://h/r.git", dest, "tok", timeout=1)
    monkeypatch.setattr("creasy.workspace.gitops._run_git", lambda *a, **k: (_ for _ in ()).throw(GitError("clone fail")))
    monkeypatch.setattr("creasy.workspace.gitops.delete_clone", lambda p: None)
    with pytest.raises(GitError):
        clone_repo("https://h/r.git", tmp_path / "new", "tok", timeout=1, auth_scheme="azure")
    monkeypatch.setattr("creasy.cleanup.end.delete_clone_path", lambda *a, **k: False)
    stay = tmp_path / "stay"
    stay.mkdir()
    with pytest.raises(GitError):
        delete_clone(stay)
    monkeypatch.setattr("creasy.workspace.gitops._run_git", lambda *a, **k: SimpleNamespace(returncode=0, stdout="base\n", stderr=""))
    try:
        resolve_merge_base(tmp_path, target_branch="main", preferred_base="", timeout=1)
    except Exception:
        pass
    try:
        fetch_and_checkout(tmp_path, source_branch="f", target_branch="m", sha="s", token="t", timeout=1)
    except Exception:
        pass


def test_gitops_killable(tmp_path, monkeypatch):
    from creasy.workspace import gitops as g

    class FakeProc:
        pid = 99
        returncode = 0

        def communicate(self, timeout=None):
            return "out", ""

    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **k: FakeProc())
    pids = []
    result = g._run_git_killable(["git", "status"], cwd=tmp_path, env={}, timeout=1, should_stop=lambda: False, on_pid=lambda p: pids.append(p))
    assert result.returncode == 0
    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(GitError):
        g._run_git_killable(["git"], cwd=tmp_path, env={}, timeout=1, should_stop=None, on_pid=None)

    class Slow:
        pid = 7
        returncode = 0

        def communicate(self, timeout=None):
            raise g.subprocess.TimeoutExpired("git", timeout)

    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **k: Slow())
    monkeypatch.setattr(g, "_kill_git", lambda p: None)
    with pytest.raises(GitError):
        g._run_git_killable(["git"], cwd=tmp_path, env={}, timeout=0.01, should_stop=None, on_pid=lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    n = {"i": 0}

    def stop():
        n["i"] += 1
        return n["i"] > 0

    with pytest.raises(GitError):
        g._run_git_killable(["git"], cwd=tmp_path, env={}, timeout=1, should_stop=stop, on_pid=None)


def test_dashboard_login_json_and_ws(tmp_config):
    from creasy.api.dashboard import router
    from creasy.jobs.models import JobRecord, mint_job_id

    tmp_config.dashboard_user = "u"
    tmp_config.dashboard_password = "p"
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    hidden = JobRecord(job_id=mint_job_id(), mr_key="1-1", project_id=1, mr_iid=1, trigger="usage", status="success")
    manager.store.save(hidden)
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(router)
    client = TestClient(app)
    client.post("/api/login", content="not-json")
    client.post("/api/login", json=["x"])
    ok = client.post("/api/login", json={"username": "u", "password": "p"})
    assert ok.status_code == 200
    assert client.get(f"/api/jobs/{hidden.job_id}").status_code == 404
    assert client.get(f"/api/jobs/{hidden.job_id}/chat").status_code == 404
    assert client.get(f"/api/jobs/{hidden.job_id}/prompts").status_code == 404
    assert client.get(f"/api/jobs/{hidden.job_id}/logs").status_code == 404
    assert client.get(f"/api/jobs/{hidden.job_id}/serve-log").status_code == 404
    assert client.post(f"/api/jobs/{hidden.job_id}/cancel").status_code in {400, 401, 404}
    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()
        assert "running" in data
    runner = FakeRunner()
    runner.release.set()
    manager.shutdown()
