"""Extra Azure client / helper coverage."""

from __future__ import annotations

import httpx
import pytest

from creasy.azure.client import (
    AzureClient,
    AzureError,
    _identity_name_values,
    _is_git_http,
    _is_http,
    _pr_labels,
    _pr_to_merge_request,
    _seg,
    _ssh_to_https,
)


def _client(handler, token: str = "pat") -> AzureClient:
    client = AzureClient("https://ado.example/tfs/DefaultCollection", token)
    client._http.close()
    client._http = httpx.Client(
        base_url="https://ado.example/tfs/DefaultCollection",
        transport=httpx.MockTransport(handler),
        headers={"Accept": "application/json"},
    )
    return client


def test_helpers_and_labels():
    assert _seg("a b") == "a%20b"
    assert _is_http("https://x")
    assert not _is_http("")
    assert _is_git_http("https://host/tfs/Col/App/_git/r")
    assert not _is_git_http("https://host/_apis/git")
    assert not _is_git_http("ssh://x")
    assert _ssh_to_https("git@host:org/repo.git") == "https://host/org/repo.git"
    assert _ssh_to_https("ssh://git@host/tfs/Col/_git/r") == "https://host/tfs/Col/_git/r"
    assert _ssh_to_https("not-ssh") == ""
    names = _identity_name_values(
        {
            "displayName": "Pat",
            "uniqueName": "DOMAIN\\pat",
            "properties": {"Account": {"$value": "pat"}, "Mail": "p@x"},
        }
    )
    assert "Pat" in names
    assert _pr_labels(None) == []
    assert "a" in _pr_labels([{"name": "a", "active": True}, {"name": "old", "active": False}, "b"])
    many = _pr_labels([f"n{i}" for i in range(25)])
    assert len(many) == 20
    mr = _pr_to_merge_request(
        {
            "pullRequestId": 3,
            "title": "t",
            "sourceRefName": "refs/heads/feat",
            "targetRefName": "refs/heads/main",
            "isDraft": True,
            "status": "active",
            "repository": {"remoteUrl": "ssh://x", "project": {"name": "App"}},
            "lastMergeSourceCommit": {"commitId": "abc"},
            "_links": {"web": {"href": "http://pr"}},
            "createdBy": {"displayName": "dev"},
            "labels": ["x"],
        }
    )
    assert mr.iid == 3
    assert mr.source_branch == "feat"


def test_apply_bind_and_send_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        path = request.url.path
        if path.endswith("/missing") and calls["n"] == 1:
            return httpx.Response(404, json={"message": "no"})
        if path.endswith("/boom"):
            return httpx.Response(500, json={"message": "nope"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert client.apply_collection("", "") == client.base_url
        same = client.apply_collection(client.base_url)
        assert same == client.base_url
        client.apply_collection("https://ado.example/tfs/Other")
        with client.bind("https://ado.example/tfs/DefaultCollection"):
            resp = client._send("GET", ["/_apis/git/repositories/missing", "/_apis/git/repositories/ok"])
            assert resp.status_code == 200
        with pytest.raises(httpx.HTTPStatusError):
            client._send("GET", ["/_apis/git/repositories/boom"])
        with pytest.raises(RuntimeError):
            client._send("GET", [])
        client._user_resolved = True
        client._user = {"id": "u"}
        assert client.current_user()["id"] == "u"
        assert client.current_user_id() == "u"
    finally:
        client.close()


def test_current_user_branches():
    def empty_token(_req):
        return httpx.Response(200, json={})

    client = _client(empty_token, token="")
    try:
        assert client.current_user() is None
        assert client.current_user_id() is None
    finally:
        client.close()

    def fail_then_user(request: httpx.Request):
        if "connectionData" in str(request.url):
            raise httpx.ConnectError("down")
        return httpx.Response(
            200,
            json={"id": "guid", "displayName": "Pat", "authenticatedUser": None},
        )

    client = _client(fail_then_user)
    try:
        user = client.current_user()
        assert user is None or user.get("id")
    finally:
        client.close()

    def no_id(request: httpx.Request):
        return httpx.Response(200, content=b"not-json")

    client = _client(no_id)
    try:
        assert client.current_user() is None
    finally:
        client.close()

    def bad_status(request: httpx.Request):
        return httpx.Response(401, json={"message": "no"})

    client = _client(bad_status)
    try:
        assert client.current_user() is None
    finally:
        client.close()

    def ok_user(request: httpx.Request):
        return httpx.Response(
            203,
            json={"authenticatedUser": {"id": "guid-1", "displayName": "Pat"}},
        )

    client = _client(ok_user)
    try:
        user = client.current_user()
        assert user["id"] == "guid-1"
        assert client.current_user_id() == "guid-1"
    finally:
        client.close()


def test_get_pr_and_reviewers_and_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pullRequests/1") and "reviewers" not in path and "iterations" not in path:
            return httpx.Response(
                200,
                json={
                    "pullRequestId": 1,
                    "title": "t",
                    "sourceRefName": "refs/heads/a",
                    "targetRefName": "refs/heads/b",
                    "repository": {
                        "remoteUrl": "ssh://git@ado/tfs/Col/App/_git/app",
                        "project": {"name": "App"},
                    },
                },
            )
        if path.endswith("/statuses"):
            return httpx.Response(200, json={"value": []})
        if path.endswith("/iterations"):
            return httpx.Response(200, json={"value": [{"id": "x"}, {"id": 2, "sourceRefCommit": {"commitId": "sha"}}]})
        if path.endswith("/repositories/repo") or path.endswith("/repositories/app"):
            return httpx.Response(500, json={"message": "no"})
        if path.endswith("/reviewers"):
            return httpx.Response(200, json=[{"id": "r1"}, "skip", {"id": "r2"}])
        return httpx.Response(404, json={"message": path})

    client = _client(handler)
    try:
        mr = client.get_pull_request("App", "app", 1)
        assert mr.sha == "sha"
        rows = client.list_reviewers("App", "app", 1, collection="https://ado.example/tfs/DefaultCollection")
        assert {r["id"] for r in rows} == {"r1", "r2"}
        client.list_reviewers("App", "app", 1)
    finally:
        client.close()

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "x"})

    client = _client(fail)
    try:
        with pytest.raises(AzureError):
            client.get_pull_request("p", "r", 1)
        with pytest.raises(AzureError):
            client.list_reviewers("p", "r", 1)
    finally:
        client.close()


def test_list_reviewers_shapes_and_status():
    def list_body(request: httpx.Request) -> httpx.Response:
        if "reviewers" in request.url.path:
            return httpx.Response(200, json=["nope"])
        if "statuses" in request.url.path:
            return httpx.Response(200, json=[{"state": "succeeded", "targetUrl": "http://ci"}])
        return httpx.Response(200, json={})

    client = _client(list_body)
    try:
        assert client.list_reviewers("p", "r", 1) == []
        from creasy.gitlab.client import MergeRequest

        mr = MergeRequest(
            project_id=0,
            iid=1,
            title="t",
            description="",
            author="",
            source_branch="a",
            target_branch="b",
            sha="s",
            base_sha="",
            start_sha="",
            web_url="",
            http_url="https://x/_git/r",
            draft=False,
            state="active",
        )
        client._attach_latest_status(mr, "p", "r", 1)
        assert mr.pipeline_status == "succeeded"
    finally:
        client.close()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    client = _client(boom)
    try:
        from creasy.gitlab.client import MergeRequest

        mr = MergeRequest(
            project_id=0,
            iid=1,
            title="t",
            description="",
            author="",
            source_branch="a",
            target_branch="b",
            sha="s",
            base_sha="",
            start_sha="",
            web_url="",
            http_url="",
            draft=False,
            state="active",
        )
        client._attach_latest_status(mr, "p", "r", 1)
    finally:
        client.close()


def test_resolve_clone_and_iterations_and_threads():
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/iterations"):
            return httpx.Response(200, content=b"")
        if path.endswith("/threads") and request.method == "GET":
            pages["n"] += 1
            headers = {}
            if pages["n"] == 1:
                headers["x-ms-continuationtoken"] = "tok"
                return httpx.Response(200, json={"value": [{"id": 1}]}, headers=headers)
            return httpx.Response(200, json=[{"id": 2}])
        if path.endswith("/threads") and request.method == "POST":
            return httpx.Response(200, json={"id": 9})
        if "/comments" in path and request.method == "POST":
            return httpx.Response(200, json={"id": 3})
        if "/comments/" in path and request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/repositories/repo"):
            return httpx.Response(200, json={"remoteUrl": "https://ado/tfs/Col/App/_git/app", "name": "app", "project": {"name": "App"}})
        return httpx.Response(404)

    client = _client(handler)
    try:
        assert client.resolve_clone_url("App", "repo", "https://ado/tfs/Col/App/_git/app").startswith("https://")
        assert client.iteration_span("p", "r", 1) == (1, 1, "")
        client.post_overview("p", "r", 1, "hi")
        client.post_file_thread("p", "r", 1, "hi", {"filePath": "/a.py"}, first_iteration=1, second_iteration=2)
        assert len(client.list_threads("p", "r", 1)) >= 2
        client.reply_to_thread("p", "r", 1, "9", "reply")
        assert client.delete_comment("p", "r", 1, "9", 3) is True
    finally:
        client.close()


def test_clone_url_fallbacks_and_errors():
    def ssh_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"remoteUrl": "ssh://git@ado/tfs/Col/App/_git/app", "name": "app", "project": {"name": "App"}},
        )

    client = _client(ssh_only)
    try:
        url = client.resolve_clone_url("App", "repo")
        assert url.startswith("https://") or "/_git/" in url
    finally:
        client.close()

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = _client(empty)
    try:
        got = client.resolve_clone_url("App", "repo", "leftover")
        assert got
        client.resolve_clone_url("App", "repo")
    finally:
        client.close()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    client = _client(boom)
    try:
        assert client.iteration_span("p", "r", 1) == (1, 1, "")
        with pytest.raises(AzureError):
            client.post_overview("p", "r", 1, "x")
        with pytest.raises(AzureError):
            client.reply_to_thread("p", "r", 1, "t", "b")
        with pytest.raises(AzureError):
            client.list_threads("p", "r", 1)
        assert client.delete_comment("p", "r", 1, "t", 1) is False
        client.resolve_clone_url("p", "r")
    finally:
        client.close()

    def status(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(404, json={"message": "gone"})
        if request.method == "POST":
            return httpx.Response(400, json={"message": "bad"})
        return httpx.Response(500, json={"message": "x"})

    client = _client(status)
    try:
        assert client.delete_comment("p", "r", 1, "t", 1) is True
        with pytest.raises(AzureError):
            client.post_file_thread("p", "r", 1, "b", {"filePath": "/x"})
    finally:
        client.close()

    def skip_threads(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"id": i} for i in range(100)]})

    client = _client(skip_threads)
    try:
        rows = client.list_threads("p", "r", 1)
        assert len(rows) >= 100
    finally:
        client.close()
