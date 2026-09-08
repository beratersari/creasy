from __future__ import annotations

from creasy.azure.events import classify_azure_webhook
from creasy.azure.identity import azure_mr_key, azure_project_num
from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger


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


def test_pr_created_enqueues_open():
    got = classify_azure_webhook({"eventType": "git.pullrequest.created", "resource": _pr()})
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "open"
    assert got.explicit is False
    assert got.provider == "azure"
    assert got.azure_project == PROJECT
    assert got.azure_repo == REPO
    assert got.mr_iid == 12
    assert got.project_id == azure_project_num(PROJECT, REPO)
    assert got.source_branch == "feat"
    assert got.target_branch == "main"
    assert got.sha == "abc123"
    assert azure_mr_key(PROJECT, REPO, 12) == f"{got.project_id}-12"


def test_pr_created_stores_collection_from_containers() -> None:
    got = classify_azure_webhook(
        {
            "eventType": "git.pullrequest.created",
            "resourceContainers": {
                "collection": {"baseUrl": "https://tfs02.company.com.tr/tfs/ExampleCollection/"}
            },
            "resource": _pr(),
        }
    )
    assert isinstance(got, ReviewTrigger)
    assert got.azure_collection == "https://tfs02.company.com.tr/tfs/ExampleCollection"


def test_pr_updated_is_ignored():
    got = classify_azure_webhook({"eventType": "git.pullrequest.updated", "resource": _pr()})
    assert isinstance(got, Ignore)
    assert got.reason == "action=update"


def test_pr_abandoned_is_cleanup():
    got = classify_azure_webhook(
        {"eventType": "git.pullrequest.updated", "resource": _pr(status="abandoned")}
    )
    assert isinstance(got, CleanupTrigger)
    assert got.action == "close"
    assert got.mr_iid == 12


def test_pr_merged_is_cleanup():
    got = classify_azure_webhook({"eventType": "git.pullrequest.merged", "resource": _pr(status="completed")})
    assert isinstance(got, CleanupTrigger)
    assert got.action == "merge"


def test_comment_review_and_ask():
    payload = {
        "eventType": "git.pullrequest.commented",
        "resource": {
            "comment": {"content": "/review focus on auth", "author": {"id": "user-1"}},
            "pullRequest": _pr(),
        },
    }
    got = classify_azure_webhook(payload, bot_user_id="bot")
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "review"
    assert got.explicit is True
    assert got.comment_text == "focus on auth"
    ask = {
        "eventType": "ms.vss-code.git-pullrequest-comment-event",
        "resource": {
            "comment": {"content": "/ask? why this lock?", "author": {"id": "user-1"}},
            "pullRequest": _pr(),
        },
    }
    got_ask = classify_azure_webhook(ask)
    assert isinstance(got_ask, ReviewTrigger)
    assert got_ask.kind == "ask"
    assert "lock" in got_ask.comment_text


def test_bot_comment_and_edit_and_empty_ask_ignored():
    bot = {
        "eventType": "git.pullrequest.commented",
        "resource": {
            "comment": {"content": "/review", "author": {"id": "bot-id"}},
            "pullRequest": _pr(),
        },
    }
    assert isinstance(classify_azure_webhook(bot, bot_user_id="bot-id"), Ignore)
    assert isinstance(classify_azure_webhook(bot, bot_user_id=None), ReviewTrigger)
    created_with_updated = {
        "eventType": "ms.vss-code.git-pullrequest-comment-event",
        "message": {"text": "Jamal Hartnett commented"},
        "resource": {
            "comment": {
                "content": "/review",
                "author": {"id": "user-1"},
                "publishedDate": "2026-01-01T00:00:00.000Z",
                "lastUpdatedDate": "2026-01-01T00:00:00.400Z",
            },
            "pullRequest": _pr(),
        },
    }
    assert isinstance(classify_azure_webhook(created_with_updated), ReviewTrigger)
    edited = {
        "eventType": "ms.vss-code.git-pullrequest-comment-event",
        "message": {"text": "Jamal Hartnett has edited a pull request comment"},
        "resource": {
            "comment": {
                "content": "/review",
                "author": {"id": "user-1"},
                "publishedDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T01:00:00Z",
                "lastContentUpdatedDate": "2026-01-01T01:00:00Z",
            },
            "pullRequest": _pr(),
        },
    }
    assert isinstance(classify_azure_webhook(edited), Ignore)
    empty = {
        "eventType": "git.pullrequest.commented",
        "resource": {"comment": {"content": "/ask   ", "author": {"id": "u"}}, "pullRequest": _pr()},
    }
    assert isinstance(classify_azure_webhook(empty), Ignore)


def test_comment_without_nested_pr_uses_links_and_containers():
    payload = {
        "eventType": "ms.vss-code.git-pullrequest-comment-event",
        "message": {"text": "Jamal commented"},
        "resourceContainers": {"project": {"id": PROJECT, "name": "App"}},
        "resource": {
            "comment": {
                "content": "/review focus on auth",
                "author": {"id": "user-1"},
                "publishedDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
                "_links": {
                    "self": {
                        "href": (
                            "https://ado.example/tfs/DefaultCollection/_apis/git"
                            f"/repositories/{REPO}/pullRequests/12/threads/5/comments/1"
                        )
                    }
                },
            }
        },
    }
    got = classify_azure_webhook(payload)
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "review"
    assert got.mr_iid == 12
    assert got.azure_repo == REPO
    assert got.azure_project == PROJECT
    assert got.comment_text == "focus on auth"


def test_draft_created_skipped_explicit_allowed():
    draft = classify_azure_webhook(
        {"eventType": "git.pullrequest.created", "resource": _pr(isDraft=True)},
        skip_drafts=True,
    )
    assert isinstance(draft, Ignore)
    note = {
        "eventType": "git.pullrequest.commented",
        "resource": {
            "comment": {"content": "/review", "author": {"id": "u"}},
            "pullRequest": _pr(isDraft=True),
        },
    }
    got = classify_azure_webhook(note, skip_drafts=True)
    assert isinstance(got, ReviewTrigger)
    assert got.explicit is True


def test_unknown_event_ignored():
    assert isinstance(classify_azure_webhook({"eventType": "workitem.created"}), Ignore)
    assert isinstance(classify_azure_webhook({}), Ignore)