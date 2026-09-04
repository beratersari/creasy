"""Replay GitLab-shaped webhooks at a running creasy server."""

from __future__ import annotations

import argparse
import json
import urllib.request


def payload(event: str, project_id: int, mr_iid: int, note: str = "") -> dict:
    if event == "mr-open":
        return {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": mr_iid,
                "target_project_id": project_id,
                "source_branch": "feat",
                "target_branch": "main",
                "draft": False,
                "url": f"http://gitlab.example/{project_id}/-/merge_requests/{mr_iid}",
            },
        }
    if event == "mr-update":
        return {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "update",
                "oldrev": "abc123",
                "iid": mr_iid,
                "target_project_id": project_id,
                "source_branch": "feat",
                "target_branch": "main",
                "draft": False,
            },
        }
    if event == "mr-close":
        return {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "close",
                "iid": mr_iid,
                "target_project_id": project_id,
            },
        }
    if event == "mr-merge":
        return {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "merge",
                "iid": mr_iid,
                "target_project_id": project_id,
            },
        }
    if event == "mr-comment":
        body = note or "/review"
        return {
            "object_kind": "note",
            "user": {"id": 1, "username": "dev"},
            "object_attributes": {"noteable_type": "MergeRequest", "note": body},
            "merge_request": {
                "iid": mr_iid,
                "target_project_id": project_id,
                "source_branch": "feat",
                "target_branch": "main",
            },
        }
    raise SystemExit(f"unknown event {event}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--url", default="http://127.0.0.1:8000/webhook")
    parser.add_argument("-s", "--secret", default="")
    parser.add_argument("--event", default="mr-open", choices=["mr-open", "mr-update", "mr-close", "mr-merge", "mr-comment"])
    parser.add_argument("--project-id", type=int, default=84969716)
    parser.add_argument("--mr-iid", type=int, default=30)
    parser.add_argument("--note", default="/review")
    args = parser.parse_args()
    body = json.dumps(payload(args.event, args.project_id, args.mr_iid, args.note)).encode()
    req = urllib.request.Request(args.url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if args.secret:
        req.add_header("X-Gitlab-Token", args.secret)
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode())


if __name__ == "__main__":
    main()
