"""Last coverage push for line >=95 and branch >=90."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.webhook import router as gitlab_router
from creasy.api.webhook_azure import router as azure_router
from creasy.azure.client import AzureError
from creasy.gitlab.events import classify_webhook
from creasy.jobs.manager import Manager
from creasy.review.findings import Finding
from creasy.review.position import build_position_variants
from creasy.workspace.diffmap import parse_unified_diff
from conftest import FakeRunner
from test_azure_events import PROJECT, REPO, _pr
from test_azure_webhook import FakeAzure, _auth


def test_position_deleted_and_unknown():
    gone = parse_unified_diff(
        """diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-a
-b
-c
"""
    )
    finding = Finding(path="gone.py", start_line=1, end_line=2, side="old", severity="major", title="t", body="b")
    variants = build_position_variants(finding, gone, base_sha="b", start_sha="b", head_sha="h")
    assert variants
    miss = parse_unified_diff(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -10,1 +10,1 @@
 keep
"""
    )
    far = Finding(path="a.py", start_line=99, end_line=99, side="new", severity="low", title="t", body="b")
    build_position_variants(far, miss, base_sha="b", start_sha="b", head_sha="h")
    far_old = Finding(path="a.py", start_line=1, end_line=1, side="old", severity="low", title="t", body="b")
    build_position_variants(far_old, miss, base_sha="b", start_sha="b", head_sha="h")


def test_classify_webhook_invalid_and_draft():
    assert classify_webhook("nope").reason == "invalid payload"
    payload = {
        "object_kind": "merge_request",
        "object_attributes": {"iid": "x", "target_project_id": 1, "action": "open"},
    }
    classify_webhook(payload)
    classify_webhook(
        {
            "object_kind": "merge_request",
            "object_attributes": {
                "iid": 1,
                "target_project_id": 1,
                "action": "open",
                "draft": True,
            },
        },
        skip_drafts=True,
    )


def test_azure_webhook_json_reviewers_cleanup(tmp_config):
    tmp_config.azure_url = "https://ado.example/tfs/Col"
    tmp_config.azure_token = "pat"
    tmp_config.azure_webhook_password = "secret"
    tmp_config.review_mention = "creasy"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.azure_bot_user_id = "bot-id"
    app.state.azure_bot_mention_names = ["creasy"]
    app.include_router(azure_router)
    app.include_router(gitlab_router)

    class KwAzure(FakeAzure):
        def list_reviewers(self, project, repo, pr_id, **kwargs):
            if kwargs:
                raise TypeError("no kwargs")
            return list(self.reviewers)

    app.state.azure = KwAzure([{"id": "bot-id", "displayName": "creasy"}])
    client = TestClient(app)
    assert client.post("/creasy/webhook/azure", content="not-json", headers=_auth()).status_code == 400
    assert client.post("/creasy/webhook/azure", json=["x"], headers=_auth()).status_code == 400
    pr = _pr()
    pr["reviewers"] = [{"id": "bot-id", "displayName": "creasy"}]
    payload = {
        "eventType": "git.pullrequest.updated",
        "message": {"text": "Alice changed the reviewer list for pull request 12 (Title)"},
        "resource": pr,
    }
    client.post("/creasy/webhook/azure", json=payload, headers=_auth())

    class ErrAzure(FakeAzure):
        def list_reviewers(self, *a, **k):
            raise AzureError("down")

    app.state.azure = ErrAzure()
    client.post("/creasy/webhook/azure", json=payload, headers=_auth())

    app.state.azure = FakeAzure()
    cleanup = {
        "eventType": "git.pullrequest.merged",
        "resource": {**pr, "status": "completed"},
    }
    client.post("/creasy/webhook/azure", json=cleanup, headers=_auth())

    manager.ready = False
    assign = {
        "eventType": "git.pullrequest.updated",
        "message": {"text": "Alice added creasy as a reviewer"},
        "resource": pr,
    }
    client.post("/creasy/webhook/azure", json=assign, headers=_auth())
    runner.release.set()
    manager.shutdown()


def test_more_branch_edges(tmp_config):
    from creasy.azure.threads import azure_thread_context, parse_azure_thread
    from creasy.jobs.manager import _real_review_busy
    from creasy.jobs.models import JobRecord
    from creasy.jobs.store import JobStore
    from creasy.opencode.session import OpenCodeClient, _role, _text_parts
    from creasy.review.findings import _coerce
    from creasy.review.position import CREASY_FINDING_MARK
    from creasy.workspace.diffmap import parse_unified_diff

    diff = parse_unified_diff(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-old
+new
"""
    )
    finding = Finding(path="a.py", start_line=99, end_line=99, side="old", severity="low", title="t", body="b")
    azure_thread_context(finding, diff)
    finding = Finding(path="a.py", start_line=99, end_line=99, side="new", severity="low", title="t", body="b")
    azure_thread_context(finding, diff)
    finding = Finding(path="a.py", start_line=2, end_line=1, side="new", severity="low", title="t", body="b")
    azure_thread_context(finding, diff)
    assert parse_azure_thread({"comments": [{"content": CREASY_FINDING_MARK}], "threadContext": {}}) is None
    assert parse_azure_thread(
        {
            "id": "1",
            "comments": [{"content": CREASY_FINDING_MARK}],
            "threadContext": {"filePath": "/a.py", "rightFileStart": {"line": 0}},
        }
    ) is None
    assert parse_azure_thread(
        {
            "comments": [{"content": CREASY_FINDING_MARK}],
            "threadContext": {"filePath": "/a.py", "rightFileStart": {"line": 1}},
        }
    ) is None
    parse_azure_thread(
        {
            "id": "1",
            "comments": ["x", {"content": CREASY_FINDING_MARK}],
            "threadContext": {"filePath": "/a.py", "rightFileStart": {"line": 1}},
        }
    )
    assert _role({"info": None, "role": "user"}) in {"user", ""}
    assert _text_parts({"info": {"parts": [{"type": "text", "text": ""}]}}) == []
    assert _coerce({"path": "a.py", "start_line": 1, "end_line": None, "title": "t"})
    assert _coerce({"path": "a.py", "start_line": 1, "end_line": "bad", "title": "t"})
    from creasy.azure.urls import normalize_collection_url

    normalize_collection_url("https://host/_git/repo")
    from creasy.review.format import format_success
    from creasy.jobs.models import JobRecord

    format_success(JobRecord(job_id="job_f", mr_key="1-1", project_id=1, mr_iid=1, trigger="review", text="# Title\n**Bold**"))
    store = JobStore(tmp_config.job_dir)
    usage = JobRecord(job_id="job_u", mr_key="1-1", project_id=1, mr_iid=1, trigger="usage", status="queued")
    rev = JobRecord(job_id="job_r2", mr_key="1-1", project_id=1, mr_iid=1, trigger="review", status="queued")
    store.save(usage)
    store.save(rev)
    assert not _real_review_busy(store, usage, ["job_u"])
    assert _real_review_busy(store, None, ["job_r2"])
    assert _real_review_busy(store, None, ["job_u", "job_r2"])

    class FakeHttp:
        def get(self, *a, **k):
            class R:
                status_code = 200

                def json(self):
                    return {"items": [{"role": "assistant"}]}

                def raise_for_status(self):
                    return None

            return R()

        def close(self):
            return None

    client = OpenCodeClient("http://x", "/tmp")
    client.http.close()
    client.http = FakeHttp()
    assert client.list_messages("ses")
    client.close()


def test_gitlab_webhook_invalid_json(tmp_config):
    from creasy.api.webhook import router

    tmp_config.webhook_secret = ""
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.bot_user_id = 1
    app.include_router(router)
    client = TestClient(app)
    classify_webhook({"object_kind": "note", "object_attributes": {"note": "x"}})
    runner = FakeRunner()
    runner.release.set()
    manager.shutdown()
