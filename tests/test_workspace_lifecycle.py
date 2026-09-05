from __future__ import annotations

import subprocess
from pathlib import Path

from creasy.workspace.gitops import (
    clone_repo,
    delete_clone,
    diff_stat,
    fetch_and_checkout,
    resolve_merge_base,
)
from creasy.workspace.identity import clone_path_for
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _init_repo(root: Path) -> Path:
    repo = root / "origin.git"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "feat")
    (repo / "app.py").write_text("print(2)\n", encoding="utf-8")
    (repo / "skip.bin").write_bytes(b"\x00\x01")
    _git(repo, "add", "app.py", "skip.bin")
    _git(repo, "commit", "-m", "change")
    _git(repo, "checkout", "main")
    return repo


def test_clone_fetch_diff_and_delete(tmp_path: Path, tmp_config):
    origin = _init_repo(tmp_path / "src")
    dest = clone_path_for(tmp_config.work_dir, "1-1")
    clone_repo(origin.as_uri(), dest, token="", timeout=60)
    sha = fetch_and_checkout(
        dest,
        source_branch="feat",
        target_branch="main",
        sha="",
        token="",
        timeout=60,
    )
    assert sha
    base = resolve_merge_base(dest, target_branch="main")
    index = diff_stat(dest, base)
    assert "app.py" in index.paths
    store = WorkspaceStore(tmp_config.data_dir / "workspace_meta")
    store.save(WorkspaceRecord(mr_key="1-1", project_id=1, mr_iid=1, clone_path=str(dest), last_sha=sha))
    assert store.get("1-1") is not None
    delete_clone(dest)
    assert not dest.exists()
    store.delete("1-1")
    assert store.get("1-1") is None


def test_rebase_onto_moved_target_uses_new_merge_base(tmp_path: Path, tmp_config):
    """Feature branched from develop; develop moved; feature rebased; review from new tip."""
    repo = tmp_path / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-b", "develop")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "core.py").write_text("core=1\n", encoding="utf-8")
    _git(repo, "add", "core.py")
    _git(repo, "commit", "-m", "develop start")
    old_fork = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "feat")
    (repo / "feature.py").write_text("feat=1\n", encoding="utf-8")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "feature work")

    _git(repo, "checkout", "develop")
    (repo / "core.py").write_text("core=2\n", encoding="utf-8")
    (repo / "later.py").write_text("later=1\n", encoding="utf-8")
    _git(repo, "add", "core.py", "later.py")
    _git(repo, "commit", "-m", "develop moved")
    new_develop = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "feat")
    _git(repo, "rebase", "develop")

    dest = clone_path_for(tmp_config.work_dir, "2-9")
    clone_repo(repo.as_uri(), dest, token="", timeout=60)
    fetch_and_checkout(
        dest,
        source_branch="feat",
        target_branch="develop",
        sha="",
        token="",
        timeout=60,
    )
    base = resolve_merge_base(dest, target_branch="develop", preferred_base=old_fork)
    assert base == new_develop
    assert base != old_fork
    index = diff_stat(dest, base)
    assert "feature.py" in index.paths
    assert "later.py" not in index.paths
    assert "core.py" not in index.paths


def test_workspace_get_returns_none_on_corrupt_json(tmp_path: Path):
    store = WorkspaceStore(tmp_path / "meta")
    path = tmp_path / "meta" / "1-1.json"
    path.write_text("{not-json", encoding="utf-8")
    assert store.get("1-1") is None


def test_workspace_save_replaces_atomically(tmp_path: Path):
    store = WorkspaceStore(tmp_path / "meta")
    store.save(WorkspaceRecord(mr_key="2-3", project_id=2, mr_iid=3, session_id="ses_a"))
    got = store.get("2-3")
    assert got is not None
    assert got.session_id == "ses_a"
    leftovers = list((tmp_path / "meta").glob("*.tmp"))
    assert leftovers == []
    store.save(WorkspaceRecord(mr_key="2-3", project_id=2, mr_iid=3, session_id="ses_b"))
    again = store.get("2-3")
    assert again is not None
    assert again.session_id == "ses_b"
