from __future__ import annotations

import httpx

from creasy.azure.client import AzureClient


def _client(handler) -> AzureClient:
    client = AzureClient("https://ado.example/tfs/DefaultCollection", "pat")
    client._http.close()
    client._http = httpx.Client(
        base_url="https://ado.example/tfs/DefaultCollection",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_get_pr_fills_sha_from_iterations_and_https_clone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pullRequests/12") and "/iterations" not in path:
            return httpx.Response(
                200,
                json={
                    "pullRequestId": 12,
                    "title": "Add overflow",
                    "sourceRefName": "refs/heads/feat",
                    "targetRefName": "refs/heads/main",
                    "repository": {
                        "id": "repo",
                        "name": "app",
                        "remoteUrl": "ssh://git@ado.example/tfs/DefaultCollection/App/_git/app",
                        "project": {"id": "proj", "name": "App"},
                    },
                },
            )
        if path.endswith("/iterations"):
            return httpx.Response(
                200,
                json={"value": [{"id": 1, "sourceRefCommit": {"commitId": "aaa"}}, {"id": 3, "sourceRefCommit": {"commitId": "bbb"}}]},
            )
        if path.endswith("/repositories/repo"):
            return httpx.Response(
                200,
                json={
                    "name": "app",
                    "remoteUrl": "https://ado.example/tfs/DefaultCollection/App/_git/app",
                    "project": {"name": "App"},
                },
            )
        return httpx.Response(404, json={"message": path})

    client = _client(handler)
    try:
        mr = client.get_pull_request("proj", "repo", 12)
        assert mr.sha == "bbb"
        assert mr.http_url.startswith("https://")
        first, second, sha = client.iteration_span("proj", "repo", 12)
        assert first == 1
        assert second == 3
        assert sha == "bbb"
    finally:
        client.close()


def test_list_threads_follows_continuation() -> None:
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        if pages["n"] == 1:
            return httpx.Response(
                200,
                json={"value": [{"id": 1, "comments": []}]},
                headers={"x-ms-continuationtoken": "next"},
            )
        return httpx.Response(200, json={"value": [{"id": 2, "comments": []}]})

    client = _client(handler)
    try:
        threads = client.list_threads("proj", "repo", 12)
        assert [t["id"] for t in threads] == [1, 2]
        assert pages["n"] == 2
    finally:
        client.close()