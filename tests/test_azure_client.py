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


def test_get_pr_retries_collection_scoped_path_on_404() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/proj/_apis/" in request.url.path:
            return httpx.Response(404, json={"message": "no project path"})
        if request.url.path.endswith("/pullRequests/26509"):
            return httpx.Response(
                200,
                json={
                    "pullRequestId": 26509,
                    "title": "Added sacmalilkarr",
                    "sourceRefName": "refs/heads/masfdasdf",
                    "targetRefName": "refs/heads/main",
                    "lastMergeSourceCommit": {"commitId": "abc"},
                    "repository": {
                        "id": "repo",
                        "name": "ProjectX",
                        "remoteUrl": "https://tfs02.company.com.tr/tfs/ExampleCollection/Example Projeleri/_git/ProjectX",
                    },
                },
            )
        return httpx.Response(404, json={"message": request.url.path})

    client = AzureClient("https://tfs02.company.com.tr", "pat")
    client._http.close()
    client._http = httpx.Client(
        base_url="https://tfs02.company.com.tr",
        transport=httpx.MockTransport(handler),
    )
    try:
        web = (
            "https://tfs02.company.com.tr/tfs/ExampleCollection/"
            "Example%20Projeleri/_git/ProjectX/pullrequest/26509"
        )
        with client.bind(web_url=web):
            mr = client.get_pull_request("f0941a9f-c740-4e13-9f9b-55ac3efc4938", "240c25cd-5cbf-4485-b91f-6d58bd8e9c68", 26509)
        assert mr.iid == 26509
        assert mr.title == "Added sacmalilkarr"
        assert any(path.startswith("/tfs/ExampleCollection/") for path in seen)
        assert any(path.endswith("/_apis/git/repositories/240c25cd-5cbf-4485-b91f-6d58bd8e9c68/pullRequests/26509") for path in seen)
    finally:
        client.close()