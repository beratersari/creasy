"""Azure DevOps Server REST client (api-version 7.1). TLS verify=False."""

from __future__ import annotations

import base64
from typing import Any, Optional
from urllib.parse import quote

import httpx

from creasy.gitlab.client import MergeRequest
from creasy.logging import get_logger, log_fail, log_ok, redact_userinfo

logger = get_logger("azure")


class AzureError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _basic_pat(token: str) -> str:
    raw = (":" + (token or "")).encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _seg(value: str) -> str:
    return quote(str(value or "").strip(), safe="")


class AzureClient:
    def __init__(self, base_url: str, token: str, *, api_version: str = "7.1", timeout: float = 30.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.api_version = api_version or "7.1"
        self.timeout = timeout
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = _basic_pat(token)
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout, verify=False)
        self._user_id: Optional[str] = None

    def close(self) -> None:
        self._http.close()

    def current_user_id(self) -> Optional[str]:
        if self._user_id is not None:
            return self._user_id
        if not self.token:
            return None
        try:
            response = self._http.get("/_apis/connectionData", params={"api-version": self.api_version})
            response.raise_for_status()
            data = response.json()
            user = data.get("authenticatedUser") if isinstance(data, dict) else None
            if isinstance(user, dict) and user.get("id"):
                self._user_id = str(user["id"])
                log_ok(
                    logger,
                    "azure current user",
                    http=response.status_code,
                    user_id=self._user_id,
                    name=user.get("providerDisplayName") or user.get("displayName") or "-",
                )
                return self._user_id
            log_fail(logger, "azure current user", http=response.status_code, reason="no authenticatedUser.id")
        except Exception as exc:  # noqa: BLE001
            log_fail(logger, "azure current user", err=exc)
        return None

    def get_pull_request(self, project: str, repo: str, pr_id: int) -> MergeRequest:
        path = f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}/pullRequests/{int(pr_id)}"
        try:
            response = self._http.get(path, params={"api-version": self.api_version})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            detail = (getattr(exc, "response", None).text or "")[:400] if getattr(exc, "response", None) else ""
            log_fail(logger, "azure GET PR", project=project, repo=repo, pr=pr_id, err=exc, body=detail)
            raise AzureError(f"fetch PR failed: {exc}") from exc
        data = response.json() if response.content else {}
        mr = _pr_to_merge_request(data)
        if not mr.sha:
            _first, _second, sha = self.iteration_span(project, repo, pr_id)
            if sha:
                logger.info("azure PR %s sha empty on GET; using iteration commit %s", pr_id, sha)
                mr.sha = sha
        if not _is_http(mr.http_url):
            built = self.resolve_clone_url(project, repo, mr.http_url)
            logger.info("azure PR %s clone url %s -> %s", pr_id, redact_userinfo(mr.http_url) or "-", redact_userinfo(built) or "-")
            mr.http_url = built
        log_ok(
            logger,
            "azure GET PR",
            project=project,
            repo=repo,
            pr=pr_id,
            title=mr.title,
            sha=mr.sha or "-",
            source=mr.source_branch or "-",
            target=mr.target_branch or "-",
            draft=mr.draft,
            state=mr.state or "-",
            clone=redact_userinfo(mr.http_url) or "-",
        )
        return mr

    def resolve_clone_url(self, project: str, repo: str, fallback: str = "") -> str:
        if _is_http(fallback):
            return fallback
        path = f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}"
        try:
            response = self._http.get(path, params={"api-version": self.api_version})
            response.raise_for_status()
            data = response.json() if response.content else {}
            log_ok(logger, "azure GET repo", project=project, repo=repo, http=response.status_code)
        except Exception as exc:  # noqa: BLE001
            log_fail(logger, "azure GET repo", project=project, repo=repo, err=exc)
            data = {}
        remote = str((data or {}).get("remoteUrl") or (data or {}).get("webUrl") or "")
        if _is_http(remote):
            log_ok(logger, "azure resolve clone url", project=project, repo=repo, source="remoteUrl", url=redact_userinfo(remote))
            return remote
        converted = _ssh_to_https(remote)
        if converted:
            log_ok(logger, "azure resolve clone url", project=project, repo=repo, source="ssh-to-https", url=redact_userinfo(converted))
            return converted
        proj = data.get("project") if isinstance((data or {}).get("project"), dict) else {}
        proj_name = str((proj or {}).get("name") or project or "").strip()
        repo_name = str((data or {}).get("name") or repo or "").strip()
        if self.base_url and proj_name and repo_name:
            built = f"{self.base_url}/{_seg(proj_name)}/_git/{_seg(repo_name)}"
            log_ok(logger, "azure resolve clone url", project=project, repo=repo, source="built", url=redact_userinfo(built))
            return built
        leftover = fallback or remote
        if leftover:
            log_ok(logger, "azure resolve clone url", project=project, repo=repo, source="fallback", url=redact_userinfo(leftover))
        else:
            log_fail(logger, "azure resolve clone url", project=project, repo=repo, reason="empty")
        return leftover

    def iteration_span(self, project: str, repo: str, pr_id: int) -> tuple[int, int, str]:
        """(firstComparingIteration, secondComparingIteration, latest source sha)."""
        path = f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}/pullRequests/{int(pr_id)}/iterations"
        try:
            response = self._http.get(path, params={"api-version": self.api_version})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log_fail(logger, "azure GET iterations", pr=pr_id, err=exc)
            return 1, 1, ""
        data = response.json() if response.content else {}
        rows = data if isinstance(data, list) else (data.get("value") if isinstance(data, dict) else [])
        ids: list[int] = []
        sha = ""
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            try:
                ids.append(int(item.get("id")))
            except (TypeError, ValueError):
                continue
            src = item.get("sourceRefCommit") if isinstance(item.get("sourceRefCommit"), dict) else {}
            commit = str(src.get("commitId") or "")
            if commit:
                sha = commit
        if not ids:
            log_ok(logger, "azure GET iterations", pr=pr_id, first=1, second=1, sha=sha or "-", count=0)
            return 1, 1, sha
        first, second = 1, max(ids)
        log_ok(logger, "azure GET iterations", pr=pr_id, first=first, second=second, sha=sha or "-", count=len(ids))
        return first, second, sha

    def post_overview(self, project: str, repo: str, pr_id: int, body: str) -> dict[str, Any]:
        return self._post_thread(project, repo, pr_id, body, thread_context=None)

    def post_file_thread(
        self,
        project: str,
        repo: str,
        pr_id: int,
        body: str,
        thread_context: dict[str, Any],
        *,
        first_iteration: int = 1,
        second_iteration: int = 1,
    ) -> dict[str, Any]:
        extra = {
            "pullRequestThreadContext": {
                "iterationContext": {
                    "firstComparingIteration": max(1, int(first_iteration or 1)),
                    "secondComparingIteration": max(1, int(second_iteration or 1)),
                }
            }
        }
        return self._post_thread(project, repo, pr_id, body, thread_context=thread_context, extra=extra)

    def _post_thread(
        self,
        project: str,
        repo: str,
        pr_id: int,
        body: str,
        thread_context: Optional[dict[str, Any]],
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        path = f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}/pullRequests/{int(pr_id)}/threads"
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": 1,
            "properties": {
                "Microsoft.TeamFoundation.Discussion.SupportsMarkdown": {
                    "$type": "System.Int32",
                    "$value": 1,
                }
            },
        }
        if thread_context:
            payload["threadContext"] = thread_context
        if extra:
            payload.update(extra)
        kind = "file" if thread_context else "overview"
        file_path = (thread_context or {}).get("filePath") or "-"
        try:
            response = self._http.post(path, params={"api-version": self.api_version}, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:400]
            log_fail(logger, "azure POST thread", kind=kind, pr=pr_id, path=file_path, http=exc.response.status_code, err=exc, body=detail)
            raise AzureError(
                f"post thread failed: {exc} {detail}",
                status_code=exc.response.status_code,
                body=detail,
            ) from exc
        except httpx.HTTPError as exc:
            log_fail(logger, "azure POST thread", kind=kind, pr=pr_id, path=file_path, err=exc)
            raise AzureError(f"post thread failed: {exc}") from exc
        data = response.json() if response.content else {}
        log_ok(
            logger,
            "azure POST thread",
            kind=kind,
            pr=pr_id,
            path=file_path,
            http=response.status_code,
            thread=(data or {}).get("id") if isinstance(data, dict) else "-",
        )
        return data

    def list_threads(self, project: str, repo: str, pr_id: int) -> list[dict[str, Any]]:
        path = f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}/pullRequests/{int(pr_id)}/threads"
        out: list[dict[str, Any]] = []
        token = ""
        skip = 0
        for page in range(1, 21):
            params: dict[str, Any] = {"api-version": self.api_version, "$top": 100}
            if token:
                params["continuationToken"] = token
            elif skip:
                params["$skip"] = skip
            try:
                response = self._http.get(path, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log_fail(logger, "azure GET threads", pr=pr_id, page=page, err=exc)
                raise AzureError(f"list threads failed: {exc}") from exc
            data = response.json() if response.content else {}
            if isinstance(data, list):
                batch = [item for item in data if isinstance(item, dict)]
            else:
                raw = data.get("value") if isinstance(data, dict) else None
                batch = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            out.extend(batch)
            nxt = (
                (response.headers.get("x-ms-continuationtoken") or response.headers.get("X-MS-ContinuationToken") or "")
                .strip()
            )
            if nxt:
                token = nxt
                skip = 0
                continue
            if len(batch) < 100:
                break
            token = ""
            skip += len(batch)
        log_ok(logger, "azure GET threads", pr=pr_id, count=len(out))
        return out

    def reply_to_thread(
        self,
        project: str,
        repo: str,
        pr_id: int,
        thread_id: str,
        body: str,
        *,
        parent_comment_id: int = 0,
    ) -> dict[str, Any]:
        path = (
            f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}"
            f"/pullRequests/{int(pr_id)}/threads/{_seg(thread_id)}/comments"
        )
        parent = int(parent_comment_id or 1)
        try:
            response = self._http.post(
                path,
                params={"api-version": self.api_version},
                json={"content": body, "parentCommentId": parent, "commentType": 1},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:400]
            log_fail(
                logger,
                "azure POST reply",
                pr=pr_id,
                thread=thread_id,
                parent=parent,
                http=exc.response.status_code,
                err=exc,
                body=detail,
            )
            raise AzureError(
                f"reply thread failed: {exc} {detail}",
                status_code=exc.response.status_code,
                body=detail,
            ) from exc
        except httpx.HTTPError as exc:
            log_fail(logger, "azure POST reply", pr=pr_id, thread=thread_id, parent=parent, err=exc)
            raise AzureError(f"reply thread failed: {exc}") from exc
        data = response.json() if response.content else {}
        log_ok(
            logger,
            "azure POST reply",
            pr=pr_id,
            thread=thread_id,
            parent=parent,
            http=response.status_code,
            comment=(data or {}).get("id") if isinstance(data, dict) else "-",
        )
        return data

    def delete_comment(self, project: str, repo: str, pr_id: int, thread_id: str, comment_id: int) -> bool:
        path = (
            f"/{_seg(project)}/_apis/git/repositories/{_seg(repo)}"
            f"/pullRequests/{int(pr_id)}/threads/{_seg(thread_id)}/comments/{int(comment_id)}"
        )
        try:
            response = self._http.delete(path, params={"api-version": self.api_version})
            if response.status_code in {200, 202, 204, 404}:
                log_ok(
                    logger,
                    "azure DELETE comment",
                    pr=pr_id,
                    thread=thread_id,
                    comment=comment_id,
                    http=response.status_code,
                )
                return True
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log_fail(logger, "azure DELETE comment", pr=pr_id, thread=thread_id, comment=comment_id, err=exc)
            return False
        log_ok(logger, "azure DELETE comment", pr=pr_id, thread=thread_id, comment=comment_id)
        return True


def _is_http(url: str) -> bool:
    return str(url or "").lower().startswith(("http://", "https://"))


def _ssh_to_https(url: str) -> str:
    text = str(url or "").strip()
    if text.startswith("git@"):
        host, _, path = text[4:].partition(":")
        if host and path:
            return f"https://{host}/{path.removeprefix('/')}"
    if text.startswith("ssh://"):
        rest = text[6:]
        if rest.startswith("git@"):
            rest = rest[4:]
        host, _, path = rest.partition("/")
        host = host.split("@")[-1]
        if host and path:
            return f"https://{host}/{path}"
    return ""


def _pr_to_merge_request(data: dict[str, Any]) -> MergeRequest:
    repo = data.get("repository") if isinstance(data.get("repository"), dict) else {}
    project = repo.get("project") if isinstance(repo.get("project"), dict) else {}
    source = str(data.get("sourceRefName") or "")
    target = str(data.get("targetRefName") or "")
    for prefix in ("refs/heads/",):
        if source.startswith(prefix):
            source = source[len(prefix) :]
        if target.startswith(prefix):
            target = target[len(prefix) :]
    last = data.get("lastMergeSourceCommit") if isinstance(data.get("lastMergeSourceCommit"), dict) else {}
    merge = data.get("lastMergeCommit") if isinstance(data.get("lastMergeCommit"), dict) else {}
    sha = str(last.get("commitId") or merge.get("commitId") or "")
    http_url = str(repo.get("remoteUrl") or repo.get("url") or "")
    links = data.get("_links") if isinstance(data.get("_links"), dict) else {}
    web = links.get("web") if isinstance(links.get("web"), dict) else {}
    author = data.get("createdBy") if isinstance(data.get("createdBy"), dict) else {}
    return MergeRequest(
        project_id=0,
        iid=int(data.get("pullRequestId") or 0),
        title=str(data.get("title") or ""),
        description=str(data.get("description") or ""),
        author=str(author.get("uniqueName") or author.get("displayName") or ""),
        source_branch=source,
        target_branch=target,
        sha=sha,
        base_sha="",
        start_sha="",
        web_url=str(web.get("href") or data.get("url") or ""),
        http_url=http_url,
        draft=bool(data.get("isDraft")),
        state=str(data.get("status") or ""),
        labels=[],
    )
