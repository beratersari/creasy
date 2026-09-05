"""Token access: GitLab HTTP 401/403/404, empty PAT, clone/fetch cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from creasy.gitlab.client import GitLabClient, GitLabError
from creasy.workspace.gitops import (
    GitError,
    _run_git,
    clone_repo,
    fetch_and_checkout,
    inject_token,
)


def _client(handler, token: str = "tok") -> GitLabClient:
    client = GitLabClient("https://gitlab.example", token)
    client._http.close()
    client._http = httpx.Client(
        base_url="https://gitlab.example/api/v4",
        headers={"PRIVATE-TOKEN": token} if token else {},
        transport=httpx.MockTransport(handler),
    )
    return client


def test_inject_token_empty_leaves_url() -> None:
    url = "https://gitlab.example/group/repo.git"
    assert inject_token(url, "") == url
    assert inject_token(url, None or "") == url


def test_clone_repo_rejects_non_http_when_token_set(tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    origin = tmp_path / "origin.git"
    origin.mkdir()
    with pytest.raises(GitError, match="http"):
        clone_repo(origin.as_uri(), dest, token="glpat-TESTONLY", timeout=10)
    assert not dest.exists()


def test_clone_repo_deletes_dest_on_auth_failure(tmp_path: Path) -> None:
    dest = tmp_path / "workspaces" / "1-1"
    with pytest.raises(GitError) as exc:
        clone_repo(
            "https://127.0.0.1:1/group/repo.git",
            dest,
            token="glpat-TESTONLY",
            timeout=15,
        )
    assert not dest.exists()
    assert "glpat-TESTONLY" not in str(exc.value)
    assert "oauth2:" not in str(exc.value)


def test_clone_repo_injects_oauth2_into_git_args(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):
        seen["args"] = list(args)
        raise GitError("git failed (128): Authentication failed")

    monkeypatch.setattr("creasy.workspace.gitops._run_git", fake_run)
    dest = tmp_path / "clone"
    with pytest.raises(GitError, match="Authentication failed"):
        clone_repo("https://gitlab.example/group/repo.git", dest, token="secret-pat", timeout=5)
    assert seen["args"][0] == "clone"
    assert seen["args"][2] == "https://oauth2:secret-pat@gitlab.example/group/repo.git"
    assert not dest.exists()


def test_clone_repo_empty_token_does_not_rewrite_url(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):
        seen["args"] = list(args)
        raise GitError("git failed (128): Authentication failed")

    monkeypatch.setattr("creasy.workspace.gitops._run_git", fake_run)
    dest = tmp_path / "clone"
    url = "https://gitlab.example/group/repo.git"
    with pytest.raises(GitError):
        clone_repo(url, dest, token="", timeout=5)
    assert seen["args"][2] == url


def test_fetch_auth_failure_scrubs_origin_and_keeps_clone(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "repo"
    dest.mkdir()
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://gitlab.example/group/repo.git"],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    original = _run_git

    def wrapped(args, **kwargs):
        if args and args[0] == "fetch":
            origin = subprocess.run(
                ["git", "config", "--local", "--get", "remote.origin.url"],
                cwd=dest,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert "oauth2:secret-pat@" in origin
            raise GitError("git failed (128): HTTP Basic: Access denied")
        return original(args, **kwargs)

    monkeypatch.setattr("creasy.workspace.gitops._run_git", wrapped)
    with pytest.raises(GitError, match="Access denied"):
        fetch_and_checkout(
            dest,
            source_branch="feat",
            target_branch="main",
            sha="abc",
            token="secret-pat",
            timeout=30,
        )
    assert dest.exists()
    origin = subprocess.run(
        ["git", "config", "--local", "--get", "remote.origin.url"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "secret-pat" not in origin
    assert "oauth2" not in origin
    assert origin == "https://gitlab.example/group/repo.git"


def test_gitlab_client_empty_token_omits_header() -> None:
    client = GitLabClient("https://gitlab.example", "")
    try:
        assert "PRIVATE-TOKEN" not in client._http.headers
    finally:
        client.close()


def test_gitlab_client_sends_private_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("PRIVATE-TOKEN", "")
        return httpx.Response(401, text='{"message":"401 Unauthorized"}')

    client = _client(handler, token="glpat-TESTONLY")
    try:
        with pytest.raises(GitLabError):
            client.get_merge_request(1, 1)
        assert seen["token"] == "glpat-TESTONLY"
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_get_merge_request_denied(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f'{{"message":"{status}"}}')

    client = _client(handler)
    try:
        with pytest.raises(GitLabError) as exc:
            client.get_merge_request(1, 2)
        assert "fetch MR failed" in str(exc.value)
        assert str(status) in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403])
def test_post_note_denied(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f'{{"message":"{status}"}}')

    client = _client(handler)
    try:
        with pytest.raises(GitLabError) as exc:
            client.post_note(1, 2, "body")
        assert "post note failed" in str(exc.value)
        assert str(status) in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403])
def test_post_discussion_denied_sets_status(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f'{{"message":"{status} forbidden"}}')

    client = _client(handler)
    try:
        with pytest.raises(GitLabError) as exc:
            client.post_discussion(1, 2, "body", {"new_path": "a.py", "new_line": 1})
        assert exc.value.status_code == status
        assert str(status) in exc.value.body
        assert "post discussion failed" in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403])
def test_list_discussions_denied(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f'{{"message":"{status}"}}')

    client = _client(handler)
    try:
        with pytest.raises(GitLabError) as exc:
            client.list_discussions(1, 2)
        assert "list discussions failed" in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403])
def test_reply_discussion_denied_sets_status(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=f'{{"message":"{status}"}}')

    client = _client(handler)
    try:
        with pytest.raises(GitLabError) as exc:
            client.reply_to_discussion(1, 2, "disc1", "body")
        assert exc.value.status_code == status
        assert "reply discussion failed" in str(exc.value)
    finally:
        client.close()


def test_current_user_id_401_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"message":"401 Unauthorized"}')

    client = _client(handler)
    try:
        assert client.current_user_id() is None
    finally:
        client.close()


def test_resolve_http_url_403_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"message":"403 Forbidden"}')

    client = _client(handler)
    try:
        assert client.resolve_http_url(99) == ""
    finally:
        client.close()
