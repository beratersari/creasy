""" /reset wipes PAT-authored notes and threads without OpenCode. """

from __future__ import annotations

from creasy.gitlab.events import ReviewTrigger, classify_webhook
from creasy.gitlab.wipe import WipeCancelled, wipe_author_comments
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore
from test_events import note_payload


class FakeGitlab:
    def __init__(self, *, user_id: int = 9) -> None:
        self.user_id = user_id
        self.discussions: list[dict] = []
        self.notes: list[dict] = []
        self.deleted_notes: list[int] = []
        self.deleted_discussion_notes: list[tuple[str, int]] = []
        self.list_discussion_calls = 0
        self.list_note_calls = 0

    def current_user_id(self) -> int:
        return self.user_id

    def list_discussions(self, project_id: int, mr_iid: int) -> list[dict]:
        self.list_discussion_calls += 1
        return list(self.discussions)

    def list_notes(self, project_id: int, mr_iid: int) -> list[dict]:
        self.list_note_calls += 1
        return list(self.notes)

    def delete_note(self, project_id: int, mr_iid: int, note_id: int) -> bool:
        self.deleted_notes.append(int(note_id))
        self.notes = [n for n in self.notes if n.get("id") != note_id]
        return True

    def delete_discussion_note(
        self,
        project_id: int,
        mr_iid: int,
        discussion_id: str,
        note_id: int,
    ) -> bool:
        self.deleted_discussion_notes.append((discussion_id, int(note_id)))
        kept = []
        for disc in self.discussions:
            if str(disc.get("id")) != discussion_id:
                kept.append(disc)
                continue
            notes = [n for n in (disc.get("notes") or []) if n.get("id") != note_id]
            if notes and disc.get("notes") and disc["notes"][0].get("id") != note_id:
                disc = dict(disc)
                disc["notes"] = notes
                kept.append(disc)
        self.discussions = kept
        return True


def _note(nid: int, author: int, *, system: bool = False, body: str = "x") -> dict:
    return {"id": nid, "author": {"id": author}, "system": system, "body": body}


def _job(**kwargs) -> JobRecord:
    data = dict(
        job_id=mint_job_id(),
        mr_key="1-1",
        project_id=1,
        mr_iid=1,
        trigger="reset",
        log_file="reset.log",
    )
    data.update(kwargs)
    return JobRecord(**data)


def test_wipe_deletes_pat_notes_and_threads_only():
    gl = FakeGitlab(user_id=9)
    gl.discussions = [
        {
            "id": "d-pat",
            "individual_note": False,
            "notes": [_note(11, 9, body="<!-- creasy-finding -->"), _note(12, 3, body="fixed")],
        },
        {
            "id": "d-human",
            "individual_note": False,
            "notes": [_note(21, 3, body="human root"), _note(22, 9, body="pat reply")],
        },
        {
            "id": "d-solo",
            "individual_note": True,
            "notes": [_note(31, 9, body="**Creasy 0.1.0 — Review**")],
        },
    ]
    gl.notes = [
        _note(31, 9, body="**Creasy 0.1.0 — Review**"),
        _note(41, 9, body="another overview"),
        _note(42, 3, body="human overview"),
        _note(43, 9, body="system", system=True),
    ]
    stats = wipe_author_comments(gl, 1, 1, 9)
    assert stats.threads == 1
    assert stats.replies == 1
    assert stats.notes == 2
    assert stats.failed == 0
    deleted_disc = {nid for _, nid in gl.deleted_discussion_notes}
    assert 11 in deleted_disc
    assert 22 in deleted_disc
    assert 41 in gl.deleted_notes
    assert 42 not in gl.deleted_notes
    assert 43 not in gl.deleted_notes
    assert 21 not in deleted_disc


def test_wipe_stops_when_cancelled():
    gl = FakeGitlab(user_id=9)
    gl.discussions = [
        {"id": "d1", "notes": [_note(1, 9)]},
        {"id": "d2", "notes": [_note(2, 9)]},
    ]
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    try:
        wipe_author_comments(gl, 1, 1, 9, should_stop=should_stop)
        raise AssertionError("expected WipeCancelled")
    except WipeCancelled:
        pass


def test_reset_job_does_not_start_opencode(tmp_config, monkeypatch):
    gl = FakeGitlab(user_id=9)
    gl.notes = [_note(5, 9, body="old review")]
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    workspaces.save(
        WorkspaceRecord(
            mr_key="1-1",
            project_id=1,
            mr_iid=1,
            session_id="ses_old",
            clone_path=str(tmp_config.work_dir / "1-1"),
        )
    )
    started = {"serve": 0}

    def boom(*args, **kwargs):
        started["serve"] += 1
        raise AssertionError("reset must not start opencode serve")

    monkeypatch.setattr("creasy.jobs.worker.start_serve", boom)
    runner = OpenCodeRunner(tmp_config, workspaces, gl)
    result = runner.run(_job(), lambda: False)
    assert started["serve"] == 0
    assert result.error == ""
    assert result.posted is True
    assert "1 note" in result.text
    assert 5 in gl.deleted_notes
    saved = workspaces.get("1-1")
    assert saved is not None
    assert saved.session_id == ""
    assert saved.clone_path.endswith("1-1")


def test_reset_job_cancelled_before_wipe(tmp_config):
    gl = FakeGitlab(user_id=9)
    gl.notes = [_note(5, 9)]
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gl)
    result = runner.run(_job(), lambda: True)
    assert result.cancelled is True
    assert gl.deleted_notes == []
    assert gl.list_note_calls == 0


def test_reset_fails_without_token_user(tmp_config):
    gl = FakeGitlab()
    gl.current_user_id = lambda: None  # type: ignore[method-assign]
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), gl)
    result = runner.run(_job(), lambda: False)
    assert result.error
    assert "GITLAB_TOKEN" in result.error
    assert gl.list_note_calls == 0


def test_webhook_reset_is_explicit():
    got = classify_webhook(note_payload("/reset"), bot_user_id=99)
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "reset"
    bot = classify_webhook(note_payload("/reset"), bot_user_id=1)
    assert not isinstance(bot, ReviewTrigger)
