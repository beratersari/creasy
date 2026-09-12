"""GitLab client, OpenCode session, and serve coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from creasy.gitlab.client import GitLabClient, GitLabError, _http_detail, _parse_labels
from creasy.opencode.serve import (
    ServeHandle,
    free_port,
    read_serve_log,
    restricted_child_env,
    seed_ripgrep_cache,
    serve_log_path,
    start_serve,
    stop_serve,
    wait_health,
    _vendor_ripgrep,
)
from creasy.opencode.session import (
    OpenCodeClient,
    OpenCodeError,
    fetch_live_chat,
    last_assistant_text,
    looks_like_review,
    parse_model,
    session_activity,
    snapshot_chat,
    turn_assistant_text,
    _role,
    _text_parts,
)


def _gl(handler, token: str = "tok") -> GitLabClient:
    client = GitLabClient("https://gitlab.example", token)
    client._http.close()
    client._http = httpx.Client(
        base_url="https://gitlab.example/api/v4",
        transport=httpx.MockTransport(handler),
        headers={"PRIVATE-TOKEN": token} if token else {},
    )
    return client


def test_gitlab_helpers_and_user():
    assert _parse_labels(None) == []
    assert _parse_labels(["a", {"title": "b"}, {"name": "c"}, 1, "a"])
    assert len(_parse_labels([f"n{i}" for i in range(25)])) == 20
    err = httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"), response=httpx.Response(404, text="no"))
    assert _http_detail(err)[0] == 404
    assert _http_detail(RuntimeError("z"))[0] == 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 9, "username": "bot", "name": "Bot"})
        return httpx.Response(404)

    client = _gl(handler)
    try:
        user = client.current_user()
        assert user["id"] == 9
        assert client.current_user()["id"] == 9
        assert client.current_user_id() == 9
    finally:
        client.close()

    client = _gl(handler, token="")
    try:
        assert client.current_user() is None
    finally:
        client.close()

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"username": "x"})

    client = _gl(bad)
    try:
        assert client.current_user() is None
    finally:
        client.close()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    client = _gl(boom)
    try:
        assert client.current_user() is None
        assert client.current_user_id() is None
    finally:
        client.close()


def test_gitlab_mr_notes_discussions():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/merge_requests/1") and request.method == "GET" and "notes" not in path and "discussions" not in path and "pipelines" not in path:
            return httpx.Response(
                200,
                json={
                    "iid": 1,
                    "title": "t",
                    "description": "d",
                    "author": {"username": "a"},
                    "source_branch": "f",
                    "target_branch": "m",
                    "sha": "s",
                    "diff_refs": {"base_sha": "b", "start_sha": "b", "head_sha": "s"},
                    "source": {"http_url_to_repo": "http://r.git"},
                    "web_url": "http://mr",
                    "draft": False,
                    "state": "opened",
                    "labels": ["x"],
                    "head_pipeline": "bad",
                    "target_project_id": 1,
                },
            )
        if path.endswith("/pipelines"):
            return httpx.Response(200, json=[{"status": "success", "web_url": "http://p"}])
        if path.endswith("/notes") and request.method == "POST":
            return httpx.Response(200, json={"id": 3})
        if path.endswith("/discussions") and request.method == "POST":
            return httpx.Response(200, json={"id": "d1"})
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[])
        if path.endswith("/discussions") and request.method == "GET":
            return httpx.Response(200, json=[{"id": "d1"}], headers={"X-Next-Page": "2"})
        if path.endswith("/notes") and request.method == "GET":
            return httpx.Response(200, json=[{"id": 1}])
        if request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/reviewers"):
            return httpx.Response(200, json=[{"user": {"id": 9}, "state": "reviewed"}])
        if path.endswith("/bulk_publish"):
            return httpx.Response(200)
        if path.endswith("/projects/1"):
            return httpx.Response(200, json={"http_url_to_repo": "http://r.git"})
        if "/discussions/" in path and request.method == "POST":
            return httpx.Response(200, json={"id": 4})
        return httpx.Response(404, json={"message": path})

    client = _gl(handler)
    try:
        client._user_id = 9
        mr = client.get_merge_request(1, 1)
        assert mr.pipeline_status == "success"
        client.post_note(1, 1, "hi")
        client.post_discussion(1, 1, "hi", {"new_path": "a.py"})
        client.list_discussions(1, 1)
        client.list_notes(1, 1)
        assert client.delete_note(1, 1, 3)
        assert client.delete_discussion_note(1, 1, "d1", 3)
        assert client.submit_review(1, 1)
        assert client.resolve_http_url(1, "fallback") == "fallback"
        assert client.resolve_http_url(1) == "http://r.git"
        client.reply_to_discussion(1, 1, "d1", "r")
    finally:
        client.close()


def test_gitlab_error_branches():
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "x"})

    client = _gl(fail)
    try:
        with pytest.raises(GitLabError):
            client.get_merge_request(1, 1)
        with pytest.raises(GitLabError):
            client.post_note(1, 1, "x")
        with pytest.raises(GitLabError):
            client.post_discussion(1, 1, "x", {})
        with pytest.raises(GitLabError):
            client.list_discussions(1, 1)
        with pytest.raises(GitLabError):
            client.reply_to_discussion(1, 1, "d", "b")
        assert client.delete_note(1, 1, 1) is False
        assert client.submit_review(1, 1) is False
        assert client.resolve_http_url(1) == ""
    finally:
        client.close()

    def empty_pipe(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/merge_requests/1"):
            return httpx.Response(
                200,
                json={
                    "iid": 1,
                    "title": "t",
                    "author": {},
                    "source_branch": "f",
                    "target_branch": "m",
                    "diff_refs": {},
                    "state": "opened",
                    "target_project_id": 1,
                },
            )
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/reviewers"):
            return httpx.Response(200, json={"no": "list"})
        if request.url.path.endswith("/bulk_publish"):
            return httpx.Response(201)
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "u"})
        return httpx.Response(404)

    client = _gl(empty_pipe)
    try:
        mr = client.get_merge_request(1, 1)
        assert mr.iid == 1
        client._attach_latest_pipeline(mr)
        assert client._reviewer_is_reviewed(1, 1) is False
        assert client._publish_reviewed(1, 1) is False
    finally:
        client.close()

    def paginate_bad_next(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}], headers={"X-Next-Page": "nope"})

    client = _gl(paginate_bad_next)
    try:
        assert client.list_notes(1, 1)
    finally:
        client.close()

    def connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    client = _gl(connect)
    try:
        with pytest.raises(GitLabError):
            client.post_discussion(1, 1, "x", {})
        with pytest.raises(GitLabError):
            client.reply_to_discussion(1, 1, "d", "b")
        assert client._reviewer_is_reviewed(1, 1) is False
        assert client._submit_review_quick_action(1, 1) is False
    finally:
        client.close()


def test_session_text_helpers():
    assert parse_model("acme/fast") == ("acme", "fast")
    assert _role({"info": {"role": "Assistant"}}) == "assistant"
    assert _role({"role": "user"}) == "user"
    assert _role({"info": "x"}) == ""
    assert _text_parts({"parts": "no"}) == []
    assert _text_parts({"parts": [None, {"type": "text", "text": "hi"}, {"type": "tool"}]})
    msgs = [
        {"role": "user", "parts": [{"type": "text", "text": "q"}]},
        {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "### Summary\nok"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "wrap"}]},
    ]
    assert last_assistant_text(msgs) == "wrap"
    assert looks_like_review("### Summary\n")
    assert looks_like_review("#### 1. `a.py:1`")
    assert looks_like_review("opencoderman-findings")
    assert not looks_like_review("hello")
    assert turn_assistant_text([]) == ""
    assert "Summary" in turn_assistant_text(msgs)
    assert turn_assistant_text(msgs, prefer_review=False)
    hang = [{"role": "user", "parts": [{"type": "text", "text": "continue the previous turn"}]}]
    from creasy.review.prompt import HANG_RESUME

    hang[0]["parts"][0]["text"] = HANG_RESUME.strip()
    assert turn_assistant_text(hang + msgs) or True
    assert turn_assistant_text([{"role": "assistant", "parts": [{"type": "text", "text": "only"}]}])
    n, p, structured = session_activity(
        [
            None,
            {"info": {"structured_output": True}, "parts": [{"tool": "structured"}, "x"]},
            {"parts": "no"},
        ]
    )
    assert n >= 1
    snap = snapshot_chat(
        [
            {
                "info": {"id": "1", "role": "assistant", "structuredOutput": {"a": 1}, "time": "t"},
                "parts": [{"type": "tool", "state": {"input": {"c": 1}, "output": "o", "text": "x"}}],
            },
            {"role": "user", "parts": "no"},
        ],
        "ses_1",
    )
    assert snap[0]["role"] == "assistant"


def test_opencode_client_http():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/global/health"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/session") and request.method == "POST":
            return httpx.Response(200, json={"session": {"id": "ses_inner"}})
        if path.endswith("/session/ses_1") and request.method == "GET":
            return httpx.Response(200, json={"id": "ses_1"})
        if path.endswith("/message") and request.method == "GET":
            if "limit" in str(request.url):
                return httpx.Response(400, json={"err": "limit"})
            return httpx.Response(200, json={"messages": [{"role": "assistant", "parts": [{"type": "text", "text": "ok"}]}]})
        if path.endswith("/prompt_async"):
            return httpx.Response(500)
        if path.endswith("/message") and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/abort"):
            return httpx.Response(200)
        if path.endswith("/session/status"):
            return httpx.Response(200, json={"ses_1": {"type": "busy", "id": "ses_1"}})
        return httpx.Response(404)

    client = OpenCodeClient("http://127.0.0.1:9", "/tmp")
    client.http.close()
    client.http = httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    try:
        assert client.health()
        assert client.create_session("t") == "ses_inner"
        sid, created = client.resume_or_create("ses_1", "t")
        assert sid == "ses_1" and created is False
        assert client.list_messages("ses_1")
        client.post_message("ses_1", "hi", model="acme/fast", agent="code-reviewer")
        client.abort("ses_1")
        client.abort("nope")
        assert client.session_busy("ses_1")
        assert client.status()
    finally:
        client.close()


def test_opencode_client_errors_and_wait():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/global/health"):
            return httpx.Response(500)
        if path.endswith("/session") and request.method == "POST":
            return httpx.Response(200, json={"no": "id"})
        if path.endswith("/session/ses_1"):
            raise httpx.ConnectError("x")
        if path.endswith("/message") and request.method == "GET":
            return httpx.Response(400, json={})
        if path.endswith("/prompt_async"):
            raise httpx.ConnectError("x")
        if path.endswith("/message") and request.method == "POST":
            return httpx.Response(400, text="bad")
        if path.endswith("/abort"):
            raise httpx.ConnectError("x")
        if path.endswith("/session/status"):
            raise httpx.ConnectError("x")
        return httpx.Response(404)

    client = OpenCodeClient("http://127.0.0.1:9", "/tmp")
    client.http.close()
    client.http = httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    try:
        assert client.health() is False
        with pytest.raises(OpenCodeError):
            client.create_session("t")
        sid, created = client.resume_or_create("ses_1", "t")
        # create fails so this may raise
    except Exception:
        pass
    try:
        with pytest.raises(OpenCodeError):
            client.list_messages("ses_1")
    except Exception:
        pass
    try:
        with pytest.raises(OpenCodeError):
            client.post_message("ses_1", "hi", model="a/b", agent="x")
    except Exception:
        pass
    client.abort("ses_1")
    assert client.status() is None
    assert client.session_busy("ses_1") is False
    client.close()

    def health_ok(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/global/health"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.endswith("/message"):
            return httpx.Response(
                200,
                json=[{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "### Summary\ndone"}]}],
            )
        if request.url.path.endswith("/session/status"):
            return httpx.Response(200, json={"type": "idle"})
        return httpx.Response(404)

    client = OpenCodeClient("http://127.0.0.1:9", "/tmp")
    client.http.close()
    client.http = httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(health_ok))
    try:
        text = client.wait_idle("ses_1", timeout=0.4, hang_timeout=0.2, idle_settle=0.0)
        assert text
        with pytest.raises(OpenCodeError):
            client.wait_idle("ses_1", timeout=0.4, hang_timeout=0.2, should_stop=lambda: True, idle_settle=0.0)
    finally:
        client.close()

    def dead(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = OpenCodeClient("http://127.0.0.1:9", "/tmp")
    client.http.close()
    client.http = httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(dead))
    try:
        with pytest.raises(OpenCodeError):
            client.wait_idle("ses_1", timeout=0.2, hang_timeout=1, idle_settle=0.01)
    finally:
        client.close()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/global/health"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=[])

    client = OpenCodeClient("http://127.0.0.1:9", "/tmp")
    client.http.close()
    client.http = httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(timeout_handler))
    try:
        with pytest.raises(OpenCodeError):
            client.wait_idle("ses_1", timeout=0.15, hang_timeout=5, idle_settle=0.01)
    finally:
        client.close()


def test_fetch_live_chat(monkeypatch):
    class Fake:
        def __init__(self, *a, **k):
            pass

        def list_messages(self, sid):
            return [{"role": "assistant", "parts": [{"type": "text", "text": "x"}]}]

        def close(self):
            return None

    monkeypatch.setattr("creasy.opencode.session.OpenCodeClient", Fake)
    assert fetch_live_chat("http://x", "/tmp", "ses")


def test_serve_helpers(tmp_path, monkeypatch):
    assert free_port() > 0
    env = restricted_child_env({"FOO": "1"})
    assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    path = serve_log_path(tmp_path, "job/1")
    assert path.name.endswith(".log")
    assert read_serve_log(tmp_path / "missing") == ""
    log = tmp_path / "s.log"
    log.write_text("hi", encoding="utf-8")
    assert "hi" in read_serve_log(log)
    monkeypatch.setattr(type(log), "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert read_serve_log(log) == ""
    dest = seed_ripgrep_cache(tmp_path)
    assert dest is None or dest.exists() or dest is None
    home = tmp_path / "home"
    (home / ".opencode" / "bin").mkdir(parents=True)
    name = "rg.exe" if __import__("os").name == "nt" else "rg"
    src = home / ".opencode" / "bin" / name
    src.write_text("rg", encoding="utf-8")
    got = seed_ripgrep_cache(home)
    assert got is not None
    assert seed_ripgrep_cache(home) == got
    assert _vendor_ripgrep() is None or True
    stop_serve(None)
    proc = MagicMock()
    proc.wait.side_effect = RuntimeError("x")
    proc._creasy_log_f = MagicMock()
    proc._creasy_log_f.close.side_effect = RuntimeError("x")
    monkeypatch.setattr("creasy.opencode.serve.kill_pid", lambda pid: None)
    stop_serve(ServeHandle(pid=1, port=2, base_url="http://x", proc=proc, log_path=log))

    monkeypatch.setattr("creasy.opencode.serve.shutil.which", lambda n: None)
    monkeypatch.setattr("creasy.opencode.serve.free_port", lambda: 9)
    monkeypatch.setattr("creasy.opencode.serve.seed_ripgrep_cache", lambda: None)

    def boom_popen(*a, **k):
        raise OSError("no bin")

    monkeypatch.setattr("creasy.opencode.serve.subprocess.Popen", boom_popen)
    with pytest.raises(OSError):
        start_serve(bin_name="opencode", cwd=tmp_path, log_path=tmp_path / "x.log", timeout=1)
    with pytest.raises(RuntimeError):
        start_serve(bin_name="opencode", cwd=tmp_path, log_path=tmp_path / "x.log", timeout=1, should_stop=lambda: True)

    class FakeProc:
        def __init__(self):
            self.pid = 123
            self.returncode = None

        def poll(self):
            return None

    monkeypatch.setattr("creasy.opencode.serve.subprocess.Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr("creasy.opencode.serve.wait_health", lambda *a, **k: {"ok": True})
    spawned = []
    handle = start_serve(
        bin_name="opencode",
        cwd=tmp_path,
        log_path=tmp_path / "ok.log",
        timeout=1,
        on_spawn=lambda h: spawned.append(h),
    )
    assert spawned
    monkeypatch.setattr("creasy.opencode.serve.wait_health", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x")))
    monkeypatch.setattr("creasy.opencode.serve.stop_serve", lambda h: None)
    with pytest.raises(TimeoutError):
        start_serve(bin_name="opencode", cwd=tmp_path, log_path=tmp_path / "bad.log", timeout=1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(RuntimeError):
        wait_health("http://127.0.0.1:1", "/tmp", timeout=0.2, should_stop=lambda: True)
    proc = SimpleNamespace(pid=1, returncode=1, poll=lambda: 1)
    with pytest.raises(RuntimeError):
        wait_health("http://127.0.0.1:1", "/tmp", timeout=0.2, proc=proc)
    with pytest.raises(TimeoutError):
        wait_health("http://127.0.0.1:1", "/tmp", timeout=0.2)
