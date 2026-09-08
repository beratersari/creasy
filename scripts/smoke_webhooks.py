"""Start Creasy webhooks and POST many fake GitLab + Azure payloads."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from creasy.api.dashboard import router as dashboard_router
from creasy.api.health import router as health_router
from creasy.api.webhook import router as gitlab_router
from creasy.api.webhook_azure import router as azure_router
from creasy.config import Config
from creasy.jobs.manager import Manager
from conftest import FakeRunner

PROJECT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REPO = "11111111-2222-3333-4444-555555555555"


def _pr(**extra):
    data = {
        "pullRequestId": 12,
        "title": "Add overflow",
        "isDraft": False,
        "status": "active",
        "sourceRefName": "refs/heads/feat",
        "targetRefName": "refs/heads/main",
        "lastMergeSourceCommit": {"commitId": "abc123"},
        "url": "http://ado/pr/12",
        "repository": {
            "id": REPO,
            "name": "app",
            "project": {"id": PROJECT, "name": "App"},
            "remoteUrl": "https://ado.example/tfs/DefaultCollection/App/_git/app",
        },
    }
    data.update(extra)
    return data


def _gitlab_mr(action: str, **attrs):
    object_attributes = {
        "action": action,
        "iid": 7,
        "target_project_id": 42,
        "source_branch": "feat",
        "target_branch": "main",
        "url": "http://gl/mr/7",
        "draft": False,
        "title": "Fix login",
        **attrs,
    }
    return {"object_kind": "merge_request", "object_attributes": object_attributes}


def _gitlab_note(note: str, user_id: int = 1):
    return {
        "object_kind": "note",
        "user": {"id": user_id},
        "object_attributes": {"noteable_type": "MergeRequest", "note": note, "action": "create"},
        "merge_request": {
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "title": "Fix login",
            "url": "http://gl/mr/7",
            "draft": False,
        },
    }


def main() -> int:
    data = ROOT / "data" / "webhook-smoke"
    cfg = Config(
        data_dir=data,
        webhook_secret="secret",
        gitlab_token="token",
        max_concurrent_jobs=2,
        skip_draft_mrs=True,
        azure_url="https://ado.example/tfs/DefaultCollection",
        azure_token="pat-test",
        azure_webhook_password="secret",
        dashboard_user="smoke",
        dashboard_password="smoke-pass",
    )
    cfg.ensure_dirs()
    runner = FakeRunner()
    manager = Manager(cfg, runner)
    manager.boot()
    app = FastAPI()
    app.state.config = cfg
    app.state.manager = manager
    app.state.gitlab = None
    app.state.azure = None
    app.state.bot_user_id = 99
    app.state.azure_bot_user_id = "bot-id"
    app.include_router(health_router)
    app.include_router(gitlab_router)
    app.include_router(azure_router)
    app.include_router(dashboard_router)

    basic = "Basic " + base64.b64encode(b":secret").decode("ascii")
    gl = {"X-Gitlab-Token": "secret"}
    az = {"Authorization": basic}

    cases = [
        ("GET", "/health", None, {}, "health"),
        ("POST", "/webhook", _gitlab_mr("open"), gl, "gitlab open"),
        ("POST", "/webhook", _gitlab_mr("update", oldrev="abc"), gl, "gitlab update+oldrev"),
        ("POST", "/webhook", _gitlab_mr("reopen"), gl, "gitlab reopen"),
        ("POST", "/webhook", _gitlab_mr("open", draft=True), gl, "gitlab draft open"),
        ("POST", "/webhook", _gitlab_note("/review."), gl, "gitlab /review."),
        ("POST", "/webhook", _gitlab_note("/ask? why nullable?"), gl, "gitlab /ask?"),
        ("POST", "/webhook", _gitlab_note("/ask   "), gl, "gitlab empty /ask"),
        ("POST", "/webhook", _gitlab_note("/reset!"), gl, "gitlab /reset!"),
        ("POST", "/webhook", _gitlab_note("looks good"), gl, "gitlab chatter"),
        ("POST", "/webhook", _gitlab_note("/review", user_id=99), gl, "gitlab bot note"),
        (
            "POST",
            "/webhook",
            {**_gitlab_note("/review"), "object_attributes": {**_gitlab_note("/review")["object_attributes"], "action": "update"}},
            gl,
            "gitlab note edit",
        ),
        ("POST", "/webhook", _gitlab_mr("close"), gl, "gitlab close"),
        ("POST", "/webhook", {"eventType": "git.pullrequest.created", "resource": _pr()}, gl, "azure body on /webhook"),
        ("POST", "/webhook", {"object_kind": "merge_request"}, {}, "gitlab missing secret"),
        ("POST", "/webhook/azure", {"eventType": "git.pullrequest.created", "resource": _pr()}, az, "azure PR created"),
        ("POST", "/webhook/azure", {"eventType": "git.pullrequest.updated", "resource": _pr()}, az, "azure PR updated"),
        (
            "POST",
            "/webhook/azure",
            {"eventType": "git.pullrequest.updated", "resource": _pr(status="abandoned")},
            az,
            "azure PR abandoned",
        ),
        (
            "POST",
            "/webhook/azure",
            {"eventType": "git.pullrequest.merged", "resource": _pr(status="completed")},
            az,
            "azure PR merged",
        ),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.commented",
                "resource": {"comment": {"content": "/review focus on auth", "author": {"id": "u1"}}, "pullRequest": _pr()},
            },
            az,
            "azure /review",
        ),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.commented",
                "resource": {"comment": {"content": "/ask? why this lock?", "author": {"id": "u1"}}, "pullRequest": _pr()},
            },
            az,
            "azure /ask?",
        ),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.commented",
                "resource": {"comment": {"content": "/ask   ", "author": {"id": "u1"}}, "pullRequest": _pr()},
            },
            az,
            "azure empty /ask",
        ),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.commented",
                "resource": {"comment": {"content": "/reset!", "author": {"id": "u1"}}, "pullRequest": _pr()},
            },
            az,
            "azure /reset!",
        ),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.commented",
                "resource": {"comment": {"content": "/review", "author": {"id": "bot-id"}}, "pullRequest": _pr()},
            },
            az,
            "azure bot comment",
        ),
        (
            "POST",
            "/webhook/azure",
            {"eventType": "git.pullrequest.created", "resource": _pr(isDraft=True)},
            az,
            "azure draft created",
        ),
        (
            "POST",
            "/webhook/azure",
            {"eventType": "workitem.created", "resource": {}},
            az,
            "azure work item",
        ),
        ("POST", "/webhook/azure", {"eventType": "git.pullrequest.created", "resource": _pr()}, {}, "azure missing secret"),
        (
            "POST",
            "/webhook/azure",
            {
                "eventType": "git.pullrequest.created",
                "resource": _pr(pullRequestId=99, title="Other repo PR"),
            },
            az,
            "azure second PR",
        ),
    ]

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=19001, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            httpx.get("http://127.0.0.1:19001/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        print("server failed to start")
        manager.shutdown()
        return 1

    print("server: http://127.0.0.1:19001  (FakeRunner, no OpenCode/git)")
    print(f"data_dir: {data}")
    print()
    rows = []
    with httpx.Client(base_url="http://127.0.0.1:19001", timeout=5.0) as client:
        for method, path, payload, headers, label in cases:
            if method == "GET":
                res = client.get(path, headers=headers)
            else:
                res = client.post(path, json=payload, headers=headers)
            try:
                body = res.json()
            except Exception:
                body = {"raw": res.text[:120]}
            status = body.get("status") if isinstance(body, dict) else None
            reason = body.get("reason") or body.get("detail") or body.get("job_id") or ""
            rows.append((res.status_code, status, label, reason))
            print(f"{res.status_code:3} {str(status):10} {label:28} {reason}")

        runner.release.set()
        time.sleep(0.3)
        locked = client.get("/api/jobs", params={"page_size": 100})
        print()
        print(f"{locked.status_code:3} {'-':10} dashboard jobs without login")
        rows.append((locked.status_code, None, "dashboard jobs without login", ""))
        login = client.post(
            "/api/login", json={"username": "smoke", "password": "smoke-pass"}
        )
        print(f"{login.status_code:3} {'-':10} dashboard login")
        rows.append((login.status_code, None, "dashboard login", ""))
        jobs_res = client.get("/api/jobs", params={"page_size": 100})
        print(f"{jobs_res.status_code:3} {'-':10} dashboard jobs after login")
        rows.append((jobs_res.status_code, None, "dashboard jobs after login", ""))
        jobs = jobs_res.json() if jobs_res.status_code == 200 else {}
        still = client.post(
            "/webhook",
            json=_gitlab_note("/review after login"),
            headers=gl,
        )
        print(
            f"{still.status_code:3} {str(still.json().get('status')):10} "
            "gitlab /review after dashboard login"
        )
        rows.append(
            (
                still.status_code,
                still.json().get("status"),
                "gitlab /review after dashboard login",
                still.json().get("job_id") or still.json().get("reason") or "",
            )
        )
    server.should_exit = True
    thread.join(timeout=5)
    print()
    print(f"jobs stored: {jobs.get('total')}")
    for job in jobs.get("jobs") or []:
        print(
            f"  {job.get('status'):10} provider={job.get('provider')} "
            f"trigger={job.get('trigger'):7} {job.get('mr_key')} {job.get('job_id')}"
        )
    manager.shutdown()
    expected_401 = {
        "gitlab missing secret",
        "azure missing secret",
        "dashboard jobs without login",
    }
    ok = True
    for code, _status, label, _reason in rows:
        if label in expected_401:
            if code != 401:
                ok = False
        elif code not in {200, 401}:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
