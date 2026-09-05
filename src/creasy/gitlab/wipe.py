"""Delete MR notes and discussion threads authored by the token user."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class WipeCancelled(Exception):
    """Caller asked the wipe to stop."""


class WipeGitlab(Protocol):
    def list_discussions(self, project_id: int, mr_iid: int) -> list[dict[str, Any]]: ...

    def list_notes(self, project_id: int, mr_iid: int) -> list[dict[str, Any]]: ...

    def delete_note(self, project_id: int, mr_iid: int, note_id: int) -> bool: ...

    def delete_discussion_note(
        self,
        project_id: int,
        mr_iid: int,
        discussion_id: str,
        note_id: int,
    ) -> bool: ...


@dataclass
class WipeStats:
    notes: int = 0
    threads: int = 0
    replies: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"removed {self.notes} note(s), {self.threads} thread(s), "
            f"{self.replies} reply(ies); failed={self.failed}"
        )


def note_author_id(note: dict[str, Any]) -> Optional[int]:
    author = note.get("author")
    raw = author.get("id") if isinstance(author, dict) else note.get("author_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _note_id(note: dict[str, Any]) -> Optional[int]:
    raw = note.get("id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_pat_user_note(note: dict[str, Any], author_id: int) -> bool:
    if note.get("system"):
        return False
    return note_author_id(note) == author_id


def _check_stop(should_stop: Optional[Callable[[], bool]]) -> None:
    if should_stop is not None and should_stop():
        raise WipeCancelled()


def wipe_author_comments(
    gitlab: WipeGitlab,
    project_id: int,
    mr_iid: int,
    author_id: int,
    should_stop: Optional[Callable[[], bool]] = None,
) -> WipeStats:
    """Delete notes and threads on this MR whose author is ``author_id``.

    A discussion whose root note is by the PAT is deleted as a thread
    (GitLab drops the whole discussion). PAT replies on someone else's
    thread are deleted one note at a time. Leftover overview notes by
    the PAT are deleted last.
    """
    stats = WipeStats()
    seen: set[int] = set()

    discussions = gitlab.list_discussions(project_id, mr_iid)
    for disc in discussions:
        _check_stop(should_stop)
        if not isinstance(disc, dict):
            continue
        notes = [n for n in (disc.get("notes") or []) if isinstance(n, dict)]
        if not notes:
            continue
        discussion_id = str(disc.get("id") or "").strip()
        first = notes[0]
        first_id = _note_id(first)
        if (
            discussion_id
            and first_id is not None
            and _is_pat_user_note(first, author_id)
        ):
            ok = gitlab.delete_discussion_note(project_id, mr_iid, discussion_id, first_id)
            if not ok:
                ok = gitlab.delete_note(project_id, mr_iid, first_id)
            if ok:
                seen.add(first_id)
                if disc.get("individual_note"):
                    stats.notes += 1
                else:
                    stats.threads += 1
            else:
                stats.failed += 1
                stats.errors.append(f"discussion {discussion_id} note {first_id}")
            continue
        for note in notes[1:]:
            _check_stop(should_stop)
            nid = _note_id(note)
            if nid is None or not _is_pat_user_note(note, author_id):
                continue
            ok = False
            if discussion_id:
                ok = gitlab.delete_discussion_note(project_id, mr_iid, discussion_id, nid)
            if not ok:
                ok = gitlab.delete_note(project_id, mr_iid, nid)
            if ok:
                seen.add(nid)
                stats.replies += 1
            else:
                stats.failed += 1
                stats.errors.append(f"reply {nid}")

    leftover = gitlab.list_notes(project_id, mr_iid)
    for note in leftover:
        _check_stop(should_stop)
        if not isinstance(note, dict):
            continue
        nid = _note_id(note)
        if nid is None or nid in seen or not _is_pat_user_note(note, author_id):
            continue
        if gitlab.delete_note(project_id, mr_iid, nid):
            stats.notes += 1
        else:
            stats.failed += 1
            stats.errors.append(f"note {nid}")
    return stats
