"""GitLab REST v4 client.

Uses ``PRIVATE-TOKEN`` and ``verify=False`` (INTENTIONAL: on-prem / TLS
intercept; no custom-CA path yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

from creasy.logging import get_logger

logger = get_logger("gitlab")


@dataclass
class MergeRequest:
    project_id: int
    iid: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    sha: str
    base_sha: str
    start_sha: str
    web_url: str
    http_url: str
    draft: bool
    state: str
    labels: list[str] = field(default_factory=list)
    pipeline_status: str = ""
    pipeline_url: str = ""


def _parse_labels(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("title") or item.get("name") or "").strip()
        else:
            name = ""
        if name and name not in out:
            out.append(name)
        if len(out) >= 20:
            break
    return out


class GitLabError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class GitLabClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        # INTENTIONAL: verify=False (on-prem / TLS intercept; no custom-CA path yet).
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token} if token else {},
            timeout=timeout,
            verify=False,
        )
        self._user_id: Optional[int] = None

    def close(self) -> None:
        self._http.close()

    def current_user_id(self) -> Optional[int]:
        if self._user_id is not None:
            return self._user_id
        if not self.token:
            return None
        try:
            response = self._http.get("/user")
            response.raise_for_status()
            data = response.json()
            self._user_id = int(data["id"])
            return self._user_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not resolve GitLab user: %s", exc)
            return None

    def get_merge_request(self, project_id: int, mr_iid: int) -> MergeRequest:
        path = f"/projects/{project_id}/merge_requests/{mr_iid}"
        try:
            response = self._http.get(path)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GitLabError(f"fetch MR failed: {exc}") from exc
        data = response.json()
        refs = data.get("diff_refs") or {}
        source = data.get("source") or {}
        last = data.get("sha") or (data.get("diff_refs") or {}).get("head_sha") or ""
        http_url = (
            source.get("http_url_to_repo")
            or source.get("git_http_url")
            or (data.get("project") or {}).get("http_url_to_repo")
            or ""
        )
        pipe = data.get("head_pipeline") or data.get("pipeline") or {}
        if not isinstance(pipe, dict):
            pipe = {}
        mr = MergeRequest(
            project_id=int(data.get("target_project_id") or project_id),
            iid=int(data["iid"]),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            author=str((data.get("author") or {}).get("username") or ""),
            source_branch=str(data.get("source_branch") or ""),
            target_branch=str(data.get("target_branch") or ""),
            sha=str(last or ""),
            base_sha=str(refs.get("base_sha") or ""),
            start_sha=str(refs.get("start_sha") or ""),
            web_url=str(data.get("web_url") or ""),
            http_url=str(http_url),
            draft=bool(data.get("draft") or data.get("work_in_progress")),
            state=str(data.get("state") or ""),
            labels=_parse_labels(data.get("labels")),
            pipeline_status=str(pipe.get("status") or "").strip(),
            pipeline_url=str(pipe.get("web_url") or "").strip(),
        )
        if not mr.pipeline_status:
            self._attach_latest_pipeline(mr)
        return mr

    def _attach_latest_pipeline(self, mr: MergeRequest) -> None:
        path = f"/projects/{mr.project_id}/merge_requests/{mr.iid}/pipelines"
        try:
            response = self._http.get(path, params={"per_page": 1})
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch MR pipelines failed: %s", exc)
            return
        batch = response.json() if response.content else []
        if not isinstance(batch, list) or not batch or not isinstance(batch[0], dict):
            return
        mr.pipeline_status = str(batch[0].get("status") or "").strip()
        mr.pipeline_url = str(batch[0].get("web_url") or "").strip()

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict[str, Any]:
        path = f"/projects/{project_id}/merge_requests/{mr_iid}/notes"
        try:
            response = self._http.post(path, json={"body": body})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GitLabError(f"post note failed: {exc}") from exc
        return response.json() if response.content else {}

    def post_discussion(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        path = f"/projects/{project_id}/merge_requests/{mr_iid}/discussions"
        try:
            response = self._http.post(path, json={"body": body, "position": position})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:400]
            raise GitLabError(
                f"post discussion failed: {exc} {detail}",
                status_code=exc.response.status_code,
                body=detail,
            ) from exc
        except httpx.HTTPError as exc:
            raise GitLabError(f"post discussion failed: {exc}") from exc
        return response.json() if response.content else {}

    def list_discussions(self, project_id: int, mr_iid: int) -> list[dict[str, Any]]:
        path = f"/projects/{project_id}/merge_requests/{mr_iid}/discussions"
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            try:
                response = self._http.get(path, params={"per_page": 100, "page": page})
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GitLabError(f"list discussions failed: {exc}") from exc
            batch = response.json() if response.content else []
            if not isinstance(batch, list) or not batch:
                break
            out.extend(item for item in batch if isinstance(item, dict))
            nxt = (response.headers.get("X-Next-Page") or "").strip()
            if not nxt:
                break
            try:
                page = int(nxt)
            except ValueError:
                break
        return out

    def reply_to_discussion(
        self,
        project_id: int,
        mr_iid: int,
        discussion_id: str,
        body: str,
    ) -> dict[str, Any]:
        disc = quote(str(discussion_id), safe="")
        path = f"/projects/{project_id}/merge_requests/{mr_iid}/discussions/{disc}/notes"
        try:
            response = self._http.post(path, json={"body": body})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = (exc.response.text or "")[:400]
            raise GitLabError(
                f"reply discussion failed: {exc} {detail}",
                status_code=exc.response.status_code,
                body=detail,
            ) from exc
        except httpx.HTTPError as exc:
            raise GitLabError(f"reply discussion failed: {exc}") from exc
        return response.json() if response.content else {}

    def resolve_http_url(self, project_id: int, fallback: str = "") -> str:
        if fallback:
            return fallback
        try:
            response = self._http.get(f"/projects/{quote(str(project_id), safe='')}")
            response.raise_for_status()
            data = response.json()
            return str(data.get("http_url_to_repo") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("resolve project url failed: %s", exc)
            return ""
