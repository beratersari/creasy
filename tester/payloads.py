"""GitLab-shaped webhook bodies for the local tester."""

from __future__ import annotations

from typing import Any

DEFAULT_PROJECT_ID = 84969716
DEFAULT_MR_IID = 30
DEFAULT_SOURCE = "feature/reviewer-bait"
DEFAULT_TARGET = "main"
DEFAULT_REPO = "beratersari0/test_project"
DEFAULT_HTTP_URL = "https://gitlab.com/beratersari0/test_project.git"
DEFAULT_WEB_URL = "https://gitlab.com/beratersari0/test_project/-/merge_requests/30"

REPOS = [
    {
        "label": "test_project (default)",
        "project_id": DEFAULT_PROJECT_ID,
        "path": DEFAULT_REPO,
        "http_url": DEFAULT_HTTP_URL,
        "default_mr_iid": DEFAULT_MR_IID,
        "source_branch": DEFAULT_SOURCE,
        "target_branch": DEFAULT_TARGET,
        "web_url": DEFAULT_WEB_URL,
    }
]

EVENTS = [
    {"id": "open", "label": "MR open", "kind": "auto"},
    {"id": "update", "label": "MR update (oldrev)", "kind": "auto"},
    {"id": "reopen", "label": "MR reopen", "kind": "auto"},
    {"id": "review", "label": "/review", "kind": "note"},
    {"id": "ask", "label": "/ask", "kind": "note"},
    {"id": "reset", "label": "/reset", "kind": "note"},
    {"id": "close", "label": "MR close", "kind": "cleanup"},
    {"id": "merge", "label": "MR merge", "kind": "cleanup"},
]


def build_payload(
    event: str,
    *,
    project_id: int,
    mr_iid: int,
    source_branch: str = DEFAULT_SOURCE,
    target_branch: str = DEFAULT_TARGET,
    note: str = "",
    user_id: int = 1,
    sha: str = "166e3a591d765358b7099a7013ec4c4296eed146",
    web_url: str = DEFAULT_WEB_URL,
) -> dict[str, Any]:
    mr_url = web_url or f"https://gitlab.example/{project_id}/-/merge_requests/{mr_iid}"
    last_commit = {"id": sha} if sha else {}
    attrs = {
        "action": event,
        "iid": mr_iid,
        "target_project_id": project_id,
        "source_project_id": project_id,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "url": mr_url,
        "draft": False,
        "last_commit": last_commit,
    }
    if event == "open":
        return {"object_kind": "merge_request", "object_attributes": attrs}
    if event == "reopen":
        attrs["action"] = "reopen"
        return {"object_kind": "merge_request", "object_attributes": attrs}
    if event == "update":
        attrs["action"] = "update"
        attrs["oldrev"] = sha or "abc123"
        return {"object_kind": "merge_request", "object_attributes": attrs}
    if event in {"close", "merge"}:
        return {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": event,
                "iid": mr_iid,
                "target_project_id": project_id,
                "url": mr_url,
            },
        }
    if event == "review":
        body = note.strip() or "/review"
        if not body.startswith("/review"):
            body = "/review " + body
        return _note(project_id, mr_iid, body, user_id, source_branch, target_branch, sha, mr_url)
    if event == "ask":
        question = note.strip() or "what is the main risk?"
        body = question if question.startswith("/ask") else f"/ask {question}"
        return _note(project_id, mr_iid, body, user_id, source_branch, target_branch, sha, mr_url)
    if event == "reset":
        body = note.strip() or "/reset"
        if not body.startswith("/reset"):
            body = "/reset"
        return _note(project_id, mr_iid, body, user_id, source_branch, target_branch, sha, mr_url)
    raise ValueError(f"unknown event {event}")


def _note(
    project_id: int,
    mr_iid: int,
    body: str,
    user_id: int,
    source_branch: str,
    target_branch: str,
    sha: str,
    mr_url: str,
) -> dict[str, Any]:
    return {
        "object_kind": "note",
        "user": {"id": user_id, "username": "tester"},
        "object_attributes": {"noteable_type": "MergeRequest", "note": body},
        "merge_request": {
            "iid": mr_iid,
            "target_project_id": project_id,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "url": mr_url,
            "last_commit": {"id": sha} if sha else {},
        },
    }
