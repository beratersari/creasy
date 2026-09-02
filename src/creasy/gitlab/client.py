from __future__ import annotations

from dataclasses import dataclass
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


class GitLabError(RuntimeError):
    pass


class GitLabClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token} if token else {},
            timeout=timeout,
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
        return MergeRequest(
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
        )

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict[str, Any]:
        path = f"/projects/{project_id}/merge_requests/{mr_iid}/notes"
        try:
            response = self._http.post(path, json={"body": body})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GitLabError(f"post note failed: {exc}") from exc
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
