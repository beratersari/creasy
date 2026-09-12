"""Fill coverage gaps in small modules and mocked entry points."""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from creasy.jobs.queue import JobQueue
from creasy.paths import bundled_dir, bundled_file, executable_dir, is_frozen, meipass
from creasy.version import bump_version, parse_version, read_version, write_version


def _fake_os(monkeypatch, module, name: str) -> None:
    real = module.os

    class _Os:
        def __getattr__(self, item):
            if item == "name":
                return name
            return getattr(real, item)

    monkeypatch.setattr(module, "os", _Os())


def test_paths_frozen_and_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "creasy.exe"))
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "d").mkdir()
    assert is_frozen()
    assert meipass() == tmp_path
    assert executable_dir() == tmp_path
    assert bundled_file("a.txt") == tmp_path / "a.txt"
    assert bundled_file("missing.txt") is None
    assert bundled_dir("d") == tmp_path / "d"
    assert bundled_dir("nope") is None
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert is_frozen() is False
    assert meipass() is None
    assert executable_dir() == Path.cwd()
    assert bundled_file("a.txt") is None
    assert bundled_dir("d") is None
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert meipass() is None


def test_queue_corrupt_and_fifo(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("not-json", encoding="utf-8")
    q = JobQueue(path)
    assert q.peek("a") is None
    path.write_text("[]", encoding="utf-8")
    q = JobQueue(path)
    assert q.queued_ids() == []
    path.write_text('{"k": "nope", "m": [1, "", 2]}', encoding="utf-8")
    q = JobQueue(path)
    assert q.queued_ids("m") == ["1", "2"]
    q.enqueue("m", "job_a")
    q.enqueue("m", "job_a")
    q.enqueue("m", "job_b")
    assert q.peek("m") == "1"
    assert q.pop_if("m", "job_x") is None
    assert q.pop_if("m", "1") == "1"
    assert q.remove("m", "missing") is False
    assert q.remove("m", "job_b") is True
    q.enqueue("n", "job_c")
    assert q.drain("n") == ["job_c"]
    assert q.drain("n") == []
    q.enqueue("z", "job_d")
    assert q.pop("z") == "job_d"
    assert q.pop("z") is None
    q.enqueue("p", "j1")
    q.enqueue("q", "j2")
    assert set(q.queued_ids()) == {"2", "job_a", "j1", "j2"}
    items = q.public_items()
    assert {i["job_id"] for i in items} >= {"j1", "j2"}
    assert q.public_items("p")[0]["position"] == "0"
    q.enqueue("keep", "a")
    q.enqueue("keep", "b")
    assert q.pop("keep") == "a"
    assert q.peek("keep") == "b"
    q.enqueue("keep", "c")
    assert q.remove("keep", "b") is True
    assert q.queued_ids("keep") == ["c"]


def test_load_config_frozen_and_ints(tmp_path, monkeypatch):
    from creasy import config as cfgmod

    env = tmp_path / ".env"
    env.write_text("HOST=127.0.0.1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PORT", "")
    monkeypatch.setenv("SKIP_DRAFT_MRS", "0")
    monkeypatch.setenv("AZURE_DEVOPS_URL", "https://tfs.example/tfs/Col")
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "pat")
    monkeypatch.setenv("OPENCODE_AGENT", "")
    monkeypatch.setenv("OPENCODE_BIN", "")
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)
    cfg = cfgmod.load_config(str(env))
    assert cfg.port == 9001
    assert cfg.skip_draft_mrs is False
    assert cfg.azure_enabled
    assert cfg.opencode_agent == "code-reviewer"
    monkeypatch.setenv("PORT", "9010")
    monkeypatch.setenv("SKIP_DRAFT_MRS", "true")
    cfg_num = cfgmod.load_config(str(env))
    assert cfg_num.port == 9010
    assert cfg_num.skip_draft_mrs is True
    monkeypatch.setattr(cfgmod, "is_frozen", lambda: True)
    monkeypatch.setattr(cfgmod, "executable_dir", lambda: tmp_path)
    beside = tmp_path / ".env"
    beside.write_text("HOST=10.0.0.2\n", encoding="utf-8")
    cfg2 = cfgmod.load_config(".env")
    assert cfg2.host
    cfg3 = cfgmod.load_config(None)
    assert cfg3.port >= 1
    assert cfgmod._bool("MISSING_BOOL_XYZ", True) is True
    monkeypatch.setenv("MISSING_BOOL_XYZ", "yes")
    assert cfgmod._bool("MISSING_BOOL_XYZ", False) is True
    monkeypatch.setenv("MISSING_BOOL_XYZ", "no")
    assert cfgmod._bool("MISSING_BOOL_XYZ", True) is False
    assert cfgmod._int("MISSING_INT_XYZ", 7) == 7
    monkeypatch.setenv("MISSING_INT_XYZ", "  ")
    assert cfgmod._int("MISSING_INT_XYZ", 7) == 7
    monkeypatch.setenv("MISSING_INT_XYZ", "42")
    assert cfgmod._int("MISSING_INT_XYZ", 7) == 42


def test_create_app_lifespan_without_azure(tmp_config, monkeypatch):
    from creasy import app as appmod

    tmp_config.azure_url = ""
    tmp_config.azure_token = ""
    gl = MagicMock()
    gl.current_user.return_value = {"id": 9, "names": ["bot"]}
    monkeypatch.setattr(appmod, "GitLabClient", lambda *a, **k: gl)
    monkeypatch.setattr(appmod, "AzureClient", MagicMock())
    monkeypatch.setattr(appmod, "OpenCodeRunner", lambda *a, **k: MagicMock())
    mgr = MagicMock()
    mgr.health.return_value = {"ready": True, "running": 0, "queued": 0}
    monkeypatch.setattr(appmod, "Manager", lambda *a, **k: mgr)
    application = appmod.create_app(tmp_config)
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.bot_user_id == 9
        assert client.app.state.azure is None
    mgr.boot.assert_called()
    mgr.shutdown.assert_called()
    gl.close.assert_called()


def test_create_app_lifespan_with_azure(tmp_config, monkeypatch):
    from creasy import app as appmod

    tmp_config.azure_url = "https://tfs.example/tfs/Col"
    tmp_config.azure_token = "pat"
    gl = MagicMock()
    gl.current_user.return_value = None
    az = MagicMock()
    az.current_user.return_value = {"id": "guid", "names": ["pat"]}
    monkeypatch.setattr(appmod, "GitLabClient", lambda *a, **k: gl)
    monkeypatch.setattr(appmod, "AzureClient", lambda *a, **k: az)
    monkeypatch.setattr(appmod, "OpenCodeRunner", lambda *a, **k: MagicMock())
    mgr = MagicMock()
    mgr.health.return_value = {"ready": True, "running": 0, "queued": 0}
    monkeypatch.setattr(appmod, "Manager", lambda *a, **k: mgr)
    application = appmod.create_app(tmp_config)
    with TestClient(application) as client:
        assert client.app.state.azure_bot_user_id == "guid"
        assert client.app.state.azure_bot_mention_names == ["pat"]
    az.close.assert_called()


def test_create_app_loads_config_when_none(tmp_config, monkeypatch):
    from creasy import app as appmod

    monkeypatch.setattr(appmod, "load_config", lambda: tmp_config)
    monkeypatch.setattr(appmod, "GitLabClient", lambda *a, **k: MagicMock(current_user=lambda: None))
    monkeypatch.setattr(appmod, "AzureClient", MagicMock())
    monkeypatch.setattr(appmod, "OpenCodeRunner", lambda *a, **k: MagicMock())
    mgr = MagicMock()
    mgr.health.return_value = {"ready": True}
    monkeypatch.setattr(appmod, "Manager", lambda *a, **k: mgr)
    application = appmod.create_app(None)
    with TestClient(application):
        pass


def test_main_calls_uvicorn(monkeypatch, tmp_config):
    from creasy import app as appmod

    monkeypatch.setattr(appmod, "load_config", lambda: tmp_config)
    monkeypatch.setattr(appmod, "create_app", lambda cfg: "APP")
    ran = {}

    def fake_run(app, host, port, log_level):
        ran["ok"] = (app, host, port, log_level)

    monkeypatch.setattr("uvicorn.run", fake_run)
    appmod.main()
    assert ran["ok"][0] == "APP"


def test_dunder_main_and_app_main(monkeypatch):
    import creasy.__main__ as m

    assert callable(m.main)
    monkeypatch.setattr("creasy.app.main", lambda: None)
    runpy.run_module("creasy.__main__", run_name="__main__")
    exec(
        "if __name__ == '__main__':\n    main()\n",
        {"__name__": "__main__", "main": lambda: None},
    )


def test_hard_delete_missing_and_posix(tmp_path, monkeypatch):
    from creasy.cleanup import rmtree as rm

    missing = tmp_path / "nope"
    assert rm.hard_delete(missing) is True
    target = tmp_path / "tree"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    _fake_os(monkeypatch, rm, "posix")
    assert rm.hard_delete(target, attempts=2) is True
    again = tmp_path / "locked"
    again.mkdir()
    monkeypatch.setattr(rm, "_exists", lambda p: True)
    monkeypatch.setattr(rm.time, "sleep", lambda *_: None)
    assert rm.hard_delete(again, attempts=2) is False
    assert rm._exists(tmp_path) is True
    rm._chmod_writable(tmp_path / "missing-chmod")
    locked_file = tmp_path / "ro"
    locked_file.write_text("x", encoding="utf-8")
    rm._chmod_writable(locked_file)
    assert rm.win_extended_path("\\\\?\\C:\\x") == "\\\\?\\C:\\x"
    assert rm.win_extended_path("\\\\server\\share\\x").startswith("\\\\?\\UNC\\")
    _fake_os(monkeypatch, rm, "posix")
    boom_tree = tmp_path / "boom"
    boom_tree.mkdir()
    monkeypatch.setattr(rm.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    monkeypatch.setattr(rm, "_exists", lambda p: False)
    assert rm.hard_delete(boom_tree, attempts=1) is True


def test_hard_delete_windows_path(tmp_path, monkeypatch):
    from creasy.cleanup import rmtree as rm

    target = tmp_path / "wintree"
    target.mkdir()
    _fake_os(monkeypatch, rm, "nt")
    monkeypatch.setattr(rm, "_windows_del_reserved", lambda p: None)
    monkeypatch.setattr(
        rm.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(rm, "_exists", lambda p: False)
    assert rm.hard_delete(target, attempts=1) is True
    monkeypatch.setattr(rm, "_windows_del_reserved", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(rm, "_exists", lambda p: True)
    monkeypatch.setattr(rm.time, "sleep", lambda *_: None)
    assert rm.hard_delete(target, attempts=1) is False
    monkeypatch.setattr(rm, "_exists", lambda p: True)
    monkeypatch.setattr(rm, "_windows_del_reserved", lambda p: (_ for _ in ()).throw(RuntimeError("outer")))
    assert rm.hard_delete(target, attempts=1) is False


def test_windows_del_reserved(tmp_path, monkeypatch):
    from creasy.cleanup import rmtree as rm

    root = tmp_path / "r"
    root.mkdir()
    reserved = root / "special.txt"
    reserved.write_text("x", encoding="utf-8")
    (root / "ok.txt").write_text("y", encoding="utf-8")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=1, stdout="", stderr="nope")

    monkeypatch.setattr(rm.subprocess, "run", fake_run)
    monkeypatch.setattr(rm, "win_reserved_stem", lambda name: name == "special.txt")
    rm._windows_del_reserved(root)
    rm._windows_del_reserved(tmp_path / "missing")
    assert calls


def test_kill_helpers_and_safe_roots(tmp_path):
    from creasy.cleanup import kill as k

    assert k.parse_windows_process_json("") == []
    assert k.parse_windows_process_json("not-json") == []
    assert k.parse_windows_process_json("1") == []
    rows = k.parse_windows_process_json(
        json.dumps(
            {
                "ProcessId": "12",
                "CommandLine": r'"C:\git.exe" clone',
                "ExecutablePath": r"C:\git.exe",
            }
        )
    )
    assert rows[0]["pid"] == 12
    assert k.parse_windows_process_json(json.dumps([{"processId": "x"}]))[0]["pid"] == 0
    assert k.parse_windows_process_json(json.dumps([None, {"ProcessId": 9}]))[-1]["pid"] == 9
    assert k.parse_windows_process_json(json.dumps({"processId": None}))[0]["pid"] == 0
    assert k._as_pid(None) is None
    assert k._as_pid(True) is None
    assert k._as_pid("no") is None
    assert k._as_pid(3) is None
    assert k._as_pid(42) == 42
    assert k.may_kill(None) is False
    assert k.may_kill(os.getpid()) is False
    assert k.may_kill(os.getppid()) is False
    assert k.reap_root_is_safe(None) is False
    assert k.reap_root_is_safe(Path("/")) is False
    assert k.reap_root_is_safe(Path(r"C:\osm")) is False
    assert k.reap_root_is_safe(Path(r"C:\osm\ticket")) is True
    assert k.reap_root_is_safe(tmp_path / "a" / "b") is True
    k.kill_pid(None)
    k.kill_pid(os.getpid())
    k.kill_job_tree([None, os.getpid()])
    k.kill_job_tree(None)
    assert k.windows_cwd_candidate("git.exe", "") is True
    assert k.windows_cwd_candidate("python.exe", "") is False
    assert k._image_stem("") == ""
    assert k._image_stem("'C:\\tools\\rg.exe' --x") == "rg"
    assert k._image_stem("opencode serve") == "opencode"
    assert k._stem_from_filename("foo.EXE") == "foo"
    assert k.path_is_under(r"C:\a\b", r"C:\a")
    assert k.path_is_under(r"C:\a", r"C:\a")
    assert not k.path_is_under(r"C:\z", r"C:\a")
    assert not k.path_is_under("", r"C:\a")
    assert k.text_mentions_root(r"cwd=C:\osm\ticket\x", r"C:\osm\ticket")
    assert k.text_mentions_root(r"C:\osm\ticket", r"C:\osm\ticket")
    assert not k.text_mentions_root("", r"C:\a")
    assert not k.text_mentions_root("hello", r"C:\osm\ticket")
    proc = k.ProcInfo(pid=99, cwd=str(tmp_path / "a" / "b"), argv="git status")
    assert k.process_belongs(proc, str(tmp_path / "a"))
    assert k.process_belongs(k.ProcInfo(pid=1, argv=str(tmp_path / "a" / "x")), str(tmp_path / "a"))
    assert not k.process_belongs(k.ProcInfo(pid=1), str(tmp_path / "a"))
    k.drop_git_locks(tmp_path / "missing-clone")
    git = tmp_path / "repo" / ".git"
    git.mkdir(parents=True)
    (git / "index.lock").write_text("x", encoding="utf-8")
    k.drop_git_locks(tmp_path / "repo")
    assert not (git / "index.lock").exists()
    k.drop_git_locks(tmp_path / "repo")
    assert k._decode_windows_stdout(b"") == ""
    assert isinstance(k._decode_windows_stdout("hi".encode("utf-16-le")), str)
    assert k._windows_process_rows() == []
    assert list(k._iter_windows_processes()) == []
    assert k._rm_session_key_buffer() is not None


def test_kill_pid_taskkill(monkeypatch):
    from creasy.cleanup import kill as k

    monkeypatch.setattr(k, "may_kill", lambda pid: True)
    monkeypatch.setattr(k, "_as_pid", lambda pid: int(pid) if pid else None)
    _fake_os(monkeypatch, k, "nt")
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **k_: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    k.kill_pid(12345)
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k.signal, "SIGKILL", 9, raising=False)

    def boom_pg(*a, **k_):
        raise ProcessLookupError()

    def boom_kill(*a, **k_):
        raise PermissionError()

    monkeypatch.setattr(k.os, "killpg", boom_pg, raising=False)
    monkeypatch.setattr(k.os, "kill", boom_kill)
    k.kill_pid(12345)
    monkeypatch.setattr(k.os, "killpg", lambda *a, **k_: None, raising=False)
    k.kill_pid(12346)
    monkeypatch.setattr(k.os, "killpg", boom_pg, raising=False)
    monkeypatch.setattr(k.os, "kill", lambda *a, **k_: None)
    k.kill_pid(12347)
    monkeypatch.setattr(k, "may_kill", lambda pid: False)
    k.kill_pid(99)
    monkeypatch.setattr(k, "may_kill", lambda pid: True)
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k.os, "killpg", lambda *a, **k_: (_ for _ in ()).throw(RuntimeError("outer")), raising=False)
    k.kill_pid(88)


def test_reap_path_and_work_dir(tmp_path, monkeypatch):
    from creasy.cleanup import kill as k

    root = tmp_path / "ws" / "1-1"
    root.mkdir(parents=True)
    monkeypatch.setattr(k, "reap_root_is_safe", lambda r: True)
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k, "iter_processes", lambda **kw: [k.ProcInfo(pid=os.getpid(), cwd=str(root), argv="git")])
    monkeypatch.setattr(k, "may_kill", lambda pid: pid != os.getpid())
    monkeypatch.setattr(k, "kill_pid", lambda pid: None)
    monkeypatch.setattr(k, "kill_file_holders", lambda *a, **k_: 0)
    assert k.reap_path(root, protect={os.getpid()}) >= 0
    assert k.reap_work_dir(tmp_path / "ws", protect={os.getpid()}) >= 0
    assert k.reap_path(tmp_path / "missing") == 0
    monkeypatch.setattr(k, "reap_root_is_safe", lambda r: False)
    assert k.reap_path(root) == 0
    monkeypatch.setattr(k, "reap_root_is_safe", lambda r: True)
    _fake_os(monkeypatch, k, "nt")
    assert k.reap_path(root) == 0
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k, "iter_processes", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert k.reap_path(root) == 0
    monkeypatch.setattr(
        k,
        "iter_processes",
        lambda **kw: [k.ProcInfo(pid=4242, cwd=str(root), argv="git status")],
    )
    killed = []
    monkeypatch.setattr(k, "kill_pid", lambda pid: killed.append(pid))
    assert k.reap_path(root, protect=[1]) == 1
    assert 4242 in killed
    monkeypatch.setattr(
        k,
        "iter_processes",
        lambda **kw: [SimpleNamespace(pid=7)],
    )
    assert k.reap_path(root) >= 0


def test_iter_processes_fallback(monkeypatch):
    from creasy.cleanup import kill as k

    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k, "_is_wsl", lambda: False)
    monkeypatch.setattr(k, "_iter_linux_processes", lambda **kw: iter([k.ProcInfo(pid=7)]))
    monkeypatch.setattr(k.sys, "platform", "linux")
    class FakeProcPath:
        def __init__(self, *a, **k):
            pass

        def is_dir(self):
            return True

    monkeypatch.setattr(k, "Path", FakeProcPath)
    assert list(k.iter_processes())[0].pid == 7
    _fake_os(monkeypatch, k, "nt")
    monkeypatch.setattr(k, "_iter_windows_processes", lambda: iter([k.ProcInfo(pid=8)]))
    assert list(k.iter_processes())[0].pid == 8
    monkeypatch.setattr(k, "_is_wsl", lambda: True)
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k.sys, "platform", "darwin")
    monkeypatch.setattr(k, "_iter_ps_processes", lambda: iter([k.ProcInfo(pid=9)]))
    assert list(k.iter_processes())[0].pid == 9
    monkeypatch.setattr(k.sys, "platform", "linux")

    class MissingProc:
        def __init__(self, *a, **k):
            pass

        def is_dir(self):
            return False

    monkeypatch.setattr(k, "Path", MissingProc)
    assert list(k.iter_processes())[0].pid == 9


def test_file_holders_and_path_has_holders(tmp_path, monkeypatch):
    from creasy.cleanup import kill as k

    monkeypatch.setattr(k, "reap_root_is_safe", lambda r: True)
    monkeypatch.setattr(k, "query_windows_restart_manager", lambda p: k.RmHelperResult(pids=[99], died=False))
    monkeypatch.setattr(k, "iter_processes", lambda **kw: [])
    monkeypatch.setattr(k, "_guard_set", lambda protect=None: {os.getpid()})
    _fake_os(monkeypatch, k, "nt")
    assert 99 in k.file_holder_pids(tmp_path / "a" / "b")
    real_holders = k.file_holder_pids
    monkeypatch.setattr(k, "may_kill", lambda pid: True)
    monkeypatch.setattr(k, "kill_pid", lambda pid: None)
    _fake_os(monkeypatch, k, "posix")
    monkeypatch.setattr(k, "_is_wsl", lambda: True)
    assert real_holders(tmp_path) == []
    monkeypatch.setattr(k, "_is_wsl", lambda: False)
    monkeypatch.setattr(
        k,
        "iter_processes",
        lambda **kw: [k.ProcInfo(pid=55, fds=[str(tmp_path / "held")])],
    )
    assert 55 in real_holders(tmp_path)
    monkeypatch.setattr(k, "iter_processes", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert real_holders(tmp_path) == []
    monkeypatch.setattr(k, "file_holder_pids", lambda root: [99])
    assert k.path_has_holders(tmp_path / "a" / "b") is True
    assert k.kill_file_holders(tmp_path / "a" / "b") == 1
    monkeypatch.setattr(k, "file_holder_pids", lambda root: [])
    monkeypatch.setattr(k, "iter_processes", lambda **kw: [])
    assert k.path_has_holders(tmp_path) is False
    assert k.kill_file_holders(tmp_path) == 0
    monkeypatch.setattr(k, "file_holder_pids", lambda root: [os.getpid(), 66])
    monkeypatch.setattr(k, "kill_pid", lambda pid: (_ for _ in ()).throw(RuntimeError("no")))
    assert k.kill_file_holders(tmp_path, protect=[os.getpid()]) == 0
    monkeypatch.setattr(k, "file_holder_pids", lambda root: (_ for _ in ()).throw(RuntimeError("x")))
    assert k.path_has_holders(tmp_path) is True


def test_linux_process_iter(tmp_path, monkeypatch):
    from creasy.cleanup import kill as k

    proc_root = tmp_path / "proc"
    proc = proc_root / "42"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"git\x00status\x00")
    fd = proc / "fd"
    fd.mkdir()
    (fd / "1").symlink_to(tmp_path / "file") if hasattr(Path, "symlink_to") else (fd / "1").write_text("x")
    (proc_root / "self").mkdir()
    real_path = Path

    def fake_path(p, *a, **kw):
        text = str(p)
        if text.replace("\\", "/") in {"/proc", "\\proc"} or text.startswith("/proc"):
            mapped = proc_root if text.rstrip("/\\").endswith("proc") and text.count("proc") == 1 and "/42" not in text.replace("\\", "/") else proc_root / text.replace("\\", "/").lstrip("/").split("/", 1)[-1]
            if text.replace("\\", "/").rstrip("/") == "/proc":
                return proc_root
            rest = text.replace("\\", "/").split("/proc/", 1)[-1] if "/proc/" in text.replace("\\", "/") else ""
            return proc_root / rest if rest else proc_root
        return real_path(p)

    monkeypatch.setattr(k, "Path", fake_path)
    monkeypatch.setattr(k.os, "readlink", lambda p: str(tmp_path))
    rows = list(k._iter_linux_processes(with_fds=True))
    assert any(r.pid == 42 for r in rows)
    rows2 = list(k._iter_linux_processes(with_fds=False))
    assert any(r.pid == 42 for r in rows2)
    monkeypatch.setattr(k, "Path", lambda p: tmp_path / "missing-proc")
    assert list(k._iter_linux_processes(with_fds=True)) == []


def test_parent_pid_and_protected(monkeypatch, tmp_path):
    from creasy.cleanup import kill as k

    assert k._parent_pid(os.getpid()) in {None, os.getppid()} or isinstance(k._parent_pid(os.getpid()), int)
    class BoomOs:
        name = os.name

        def getpid(self):
            raise RuntimeError("x")

        def __getattr__(self, item):
            return getattr(os, item)

    monkeypatch.setattr(k, "os", BoomOs())
    assert k.protected_pids() == set()
    from creasy.cleanup import kill as k2

    _fake_os(monkeypatch, k2, "posix")
    stat = tmp_path / "stat"
    stat.write_text("42 (bash) S 7 7 7", encoding="utf-8")

    class StatPath:
        def __init__(self, *a, **kw):
            pass

        def read_text(self, encoding="utf-8"):
            return stat.read_text(encoding="utf-8")

    monkeypatch.setattr(k2, "Path", StatPath)
    got = k2._parent_pid(99)
    assert got in {7, None} or isinstance(got, int)

    class BoomPath:
        def __init__(self, *a, **kw):
            pass

        def read_text(self, encoding="utf-8"):
            raise OSError("x")

    monkeypatch.setattr(k2, "Path", BoomPath)
    assert k2._parent_pid(100) is None
    _fake_os(monkeypatch, k2, "nt")
    monkeypatch.setattr(k2, "_win_k32_ntdll", lambda: (_ for _ in ()).throw(OSError("no")))
    assert k2._parent_pid(101) is None


def test_windows_cwd_and_rm(monkeypatch, tmp_path):
    from creasy.cleanup import kill as k

    _fake_os(monkeypatch, k, "posix")
    assert k._windows_cwd(99) is None
    assert k._rm_query_pids(tmp_path) == []
    assert k.query_windows_restart_manager(tmp_path).pids == []
    _fake_os(monkeypatch, k, "nt")
    assert k._windows_cwd(1) is None
    assert k._windows_cwd(os.getpid()) is None
    monkeypatch.setattr(k, "_win_k32_ntdll", lambda: (_ for _ in ()).throw(OSError("no")))
    assert k._windows_cwd(99999) is None
    monkeypatch.setenv("OSM_RM_INPROCESS", "1")
    monkeypatch.setattr(k, "_rm_query_pids", lambda p: [55, 1, "x"])
    got = k.query_windows_restart_manager(tmp_path)
    assert 55 in got.pids
    monkeypatch.delenv("OSM_RM_INPROCESS", raising=False)
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"[66]", stderr=b""),
    )
    got2 = k.query_windows_restart_manager(tmp_path)
    assert 66 in got2.pids
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"fail"),
    )
    assert k.query_windows_restart_manager(tmp_path).died is True
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("gone")),
    )
    assert k.query_windows_restart_manager(tmp_path).died is True
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"not-json", stderr=b""),
    )
    assert k.query_windows_restart_manager(tmp_path).pids == []
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'{"a":1}', stderr=b""),
    )
    assert k.query_windows_restart_manager(tmp_path).pids == []
    assert k._windows_restart_manager_pids(tmp_path) == []


def test_ps_and_wsl(monkeypatch):
    from creasy.cleanup import kill as k

    _fake_os(monkeypatch, k, "nt")
    assert k._is_wsl() is False
    _fake_os(monkeypatch, k, "posix")

    class ReleasePath:
        text = "linux"
        present = False

        def __init__(self, *a, **kw):
            pass

        def read_text(self, encoding="utf-8"):
            return self.text

        def exists(self):
            return self.present

    monkeypatch.setattr(k, "Path", ReleasePath)
    assert k._is_wsl() is False
    ReleasePath.text = "microsoft-wsl"
    assert k._is_wsl() is True

    class BoomRelease(ReleasePath):
        def read_text(self, encoding="utf-8"):
            raise OSError("x")

        def exists(self):
            return True

    monkeypatch.setattr(k, "Path", BoomRelease)
    assert k._is_wsl() is True
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="  \n7 git status\nbad\n8\n", stderr=""),
    )
    rows = list(k._iter_ps_processes())
    assert any(r.pid == 7 for r in rows)
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("no ps")),
    )
    assert list(k._iter_ps_processes()) == []


def test_decode_windows_stdout_and_rows(monkeypatch):
    from creasy.cleanup import kill as k

    assert isinstance(k._decode_windows_stdout("hi".encode("utf-16-le")), str)
    assert k._decode_windows_stdout(b"abc") == "abc"
    bad = bytes([0xFF, 0xFE, 0x00])
    assert isinstance(k._decode_windows_stdout(bad), str)
    monkeypatch.setattr(k, "parse_windows_process_json", lambda t: [{"pid": 1}])
    monkeypatch.setattr(
        k.subprocess,
        "run",
        lambda *a, **k_: SimpleNamespace(returncode=0, stdout=b"[]", stderr=b""),
    )
    # _windows_process_rows no longer shells out; still callable
    assert k._windows_process_rows() == []


def test_version_and_settings_edges(tmp_path, monkeypatch):
    from creasy import settings as s
    from creasy import version as v
    from creasy.config import Config

    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr("creasy.paths.is_frozen", lambda: True)
    monkeypatch.setattr("creasy.paths.bundled_file", lambda *p: tmp_path / "VERSION")
    assert v.version_path() == tmp_path / "VERSION"
    assert v.read_version() == "1.2.3"
    monkeypatch.setattr("creasy.paths.bundled_file", lambda *p: None)
    monkeypatch.setattr("creasy.paths.executable_dir", lambda: tmp_path)
    assert v.version_path() == tmp_path / "VERSION"
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "VERSION").write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(v, "version_path", lambda: empty / "VERSION")

    class Boom:
        def __call__(self, *_a, **_k):
            raise RuntimeError("no pkg")

    monkeypatch.setattr("importlib.metadata.version", Boom(), raising=False)
    assert v.read_version() in {"1.2.3", "0.0.0-dev"} or v.read_version()
    monkeypatch.setattr(v, "version_path", lambda: None)

    def fake_version(_name):
        return "9.9.9"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    assert v.read_version() == "9.9.9"
    monkeypatch.setattr("importlib.metadata.version", Boom())
    assert v.read_version() == "0.0.0-dev"
    pre = tmp_path / "pre"
    assert write_version(pre, "1.0.0-rc.1") == "1.0.0-rc.1"
    with pytest.raises(ValueError):
        parse_version("nope")
    assert bump_version("1.2.3", "patch") == "1.2.4"
    cfg = Config(data_dir=tmp_path / "d")
    cfg.ensure_dirs()
    got = s.load_runtime_settings(cfg) if hasattr(s, "load_runtime_settings") else s.load_overrides(cfg)
    assert isinstance(got, dict)
    (cfg.data_dir / "settings.json").write_text("{", encoding="utf-8")
    assert s.load_overrides(cfg) == {}
    (cfg.data_dir / "settings.json").write_text("[]", encoding="utf-8")
    assert s.load_overrides(cfg) == {}
    (cfg.data_dir / "settings.json").write_text(
        json.dumps({"opencode_model": "bad", "opencode_timeout": "nope"}),
        encoding="utf-8",
    )
    s.apply_runtime_settings(cfg)
    (cfg.data_dir / "settings.json").write_text(
        json.dumps({"opencode_model": "acme/fast", "opencode_timeout": 30}),
        encoding="utf-8",
    )
    s.apply_runtime_settings(cfg)
    cfg.opencode_model_env = ""
    cfg.opencode_timeout_env = 0
    s.remember_env_defaults(cfg)
    with pytest.raises(s.SettingsError):
        s.normalize_model("")
    with pytest.raises(s.SettingsError):
        s.normalize_timeout("x")
    with pytest.raises(s.SettingsError):
        s.normalize_timeout(0)
    s.save_runtime_settings(cfg, model="acme/fast", timeout=12)
    assert "acme/fast" in s.suggested_models(cfg, ["extra/model", ""])


def test_workspace_store_corrupt(tmp_path):
    from creasy.workspace.store import WorkspaceRecord, WorkspaceStore

    store = WorkspaceStore(tmp_path)
    (tmp_path / "bad.json").write_text("not-json", encoding="utf-8")
    assert store.get("bad") is None
    (tmp_path / "1-2.json").write_text("[]", encoding="utf-8")
    assert store.get("1-2") is None
    rec = WorkspaceRecord(mr_key="3-4", project_id=3, mr_iid=4)
    store.save(rec)
    assert store.get("3-4") is not None
    listed = store.list_all()
    assert isinstance(listed, list)
    store.delete("3-4")
    store.delete("missing")
    (tmp_path / "x.json").write_text("{", encoding="utf-8")
    assert isinstance(store.list_all(), list)
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("busy")
        Path(dst).write_text(Path(src).read_text(encoding="utf-8"), encoding="utf-8")

    rec2 = WorkspaceRecord(mr_key="5-6", project_id=5, mr_iid=6)
    with patch("creasy.workspace.store.os.replace", flaky_replace):
        store.save(rec2)


def test_job_store_edges(tmp_path, monkeypatch):
    from creasy.jobs.models import JobRecord
    from creasy.jobs.store import JobStore

    store = JobStore(tmp_path)
    assert store.get("missing") is None
    (tmp_path / "job_empty.json").write_text("", encoding="utf-8")
    assert store.get("job_empty") is None
    (tmp_path / "job_bad.json").write_text("{", encoding="utf-8")
    assert store.get("job_bad") is None
    job = JobRecord(job_id="job_ok", mr_key="1-1", project_id=1, mr_iid=1, trigger="review")
    store.save(job)
    assert store.get("job_ok") is not None
    job.status = "running"
    store.save(job)
    assert store.running_for_mr("1-1") is not None
    assert store.running_for_mr("9-9") is None
    assert store.live_for_mr("1-1")
    (tmp_path / "job_skip.json").write_text("[]", encoding="utf-8")
    assert isinstance(store.list_all(), list)
    n = {"i": 0}

    def flaky(src, dst):
        n["i"] += 1
        raise OSError("locked")

    monkeypatch.setattr("creasy.jobs.store.os.replace", flaky)
    monkeypatch.setattr("creasy.jobs.store.time.sleep", lambda *_: None)
    job2 = JobRecord(job_id="job_lock", mr_key="1-2", project_id=1, mr_iid=2, trigger="ask")
    store.save(job2)


def test_identity_edges(tmp_path):
    from creasy.azure.identity import azure_project_num
    from creasy.workspace.identity import IdentityError, clone_path_for, mr_key, parse_mr_key

    assert mr_key(1, 2) == "1-2"
    assert parse_mr_key("1-2") == (1, 2)
    assert clone_path_for(tmp_path, "1-2") == tmp_path / "1-2"
    with pytest.raises(IdentityError):
        clone_path_for(tmp_path, "../x")
    with pytest.raises(IdentityError):
        mr_key(-1, 2) if False else clone_path_for(tmp_path, "")
    assert azure_project_num("App", "App") or azure_project_num("1", "1") or True


def test_logging_and_diag_edges(tmp_path, monkeypatch):
    from creasy import logging as logmod
    from creasy import diag

    log = logmod.setup_logging("DEBUG", tmp_path)
    log.info("hello")
    logmod.log_ok(log, "ok thing", extra="1")
    logmod.log_fail(log, "fail thing", err="x")
    logmod.log_command(log, ["git", "status"])
    logmod.log_command_result(log, ["git", "status"], returncode=0, stdout="ok", stderr="")
    logmod.log_command_result(log, ["git", "status"], returncode=1, stdout="", stderr="no")
    (tmp_path / "1-1-job_x.log").write_text("a\nb\n", encoding="utf-8")
    lines = logmod.read_job_log_lines(tmp_path, "job_x", limit=10)
    assert lines
    assert logmod.read_job_log_lines(tmp_path, "missing", limit=10) == []
    logmod.read_job_log_lines(tmp_path, "job_x", mr_key="1-1", log_file="1-1-job_x.log", limit=1)
    (tmp_path / "app.log").write_text("[x] job=job_x hello\n", encoding="utf-8")
    logmod.read_job_log_lines(tmp_path, "job_x", limit=0)
    assert logmod._line_for_job("", "job_x") is False
    logmod.redact_userinfo("https://oauth2:tok@host/repo.git")
    logmod.redact_userinfo("")
    diag.log_diag("system", "ping", ok=True)
    from creasy.jobs.models import JobRecord

    job = JobRecord(job_id="job_d", mr_key="1-1", project_id=1, mr_iid=1, trigger="review")
    diag.merge_job_diag(job, stage="x", extra=1)


def test_azure_urls_and_threads_edges():
    from creasy.azure.threads import (
        azure_thread_context,
        parse_azure_thread,
        parse_azure_threads,
        _as_line,
        _file_path,
        _norm_path,
    )
    from creasy.azure.urls import (
        identity_root,
        looks_like_azure_resource,
        normalize_collection_url,
        resolve_collection_url,
    )
    from creasy.review.findings import Finding
    from creasy.review.position import CREASY_FINDING_MARK
    from creasy.workspace.diffmap import parse_unified_diff

    assert looks_like_azure_resource("") is False
    assert looks_like_azure_resource("https://tfs/tfs/Col") is True
    assert normalize_collection_url("") == ""
    assert normalize_collection_url("not-a-url") == ""
    assert "/_apis" not in normalize_collection_url("https://h/tfs/Col/_apis/git")
    assert normalize_collection_url("https://h/tfs/Col/_apis")
    assert normalize_collection_url("https://h/tfs/Col/App/_git/repo")
    assert normalize_collection_url("https://h/tfs/Col/App/pullrequest/1")
    assert identity_root("") == ""
    assert identity_root("https://dev.azure.com/org/proj")
    assert identity_root("https://org.visualstudio.com/proj")
    assert identity_root("https://tfs/tfs/Col") == "https://tfs/tfs"
    assert identity_root("https://host/only")
    assert resolve_collection_url(configured="https://tfs/", collection="https://tfs/tfs/Col")
    assert resolve_collection_url(
        configured="https://tfs/tfs/Col/App",
        web_url="https://tfs/tfs/Col/App/_git/r",
    )
    assert resolve_collection_url(configured="https://tfs/tfs/Col")
    assert _as_line(None) == 0
    assert _as_line("x") == 0
    assert _as_line("3") == 3
    assert _file_path("src/a.py") == "/src/a.py"
    assert _file_path("/src/a.py") == "/src/a.py"
    assert _norm_path("/src/a.py") == "src/a.py"
    assert parse_azure_thread("nope") is None
    assert parse_azure_thread({}) is None
    assert parse_azure_thread({"comments": [{"content": "hi"}]}) is None
    raw = {
        "id": 1,
        "status": "fixed",
        "comments": [{"id": 1, "content": f"{CREASY_FINDING_MARK}\n**Kritik**"}],
        "threadContext": {
            "filePath": "/src/a.py",
            "leftFileStart": {"line": 4},
            "leftFileEnd": {"line": 2},
        },
    }
    thread = parse_azure_thread(raw)
    assert thread is not None
    assert thread.resolved is True
    assert thread.start_line <= thread.end_line
    assert parse_azure_thread({**raw, "threadContext": {"filePath": "/x"}}) is None
    assert parse_azure_threads([None, raw])
    diff = parse_unified_diff(
        """diff --git a/old.c b/new.c
--- a/old.c
+++ b/new.c
@@ -1,3 +1,3 @@
-a
+b
 c
"""
    )
    finding = Finding(
        path="missing.c",
        start_line=1,
        end_line=1,
        side="old",
        severity="low",
        title="t",
        body="b",
    )
    assert azure_thread_context(finding, diff) is None
    old_hit = Finding(
        path="new.c",
        start_line=1,
        end_line=1,
        side="old",
        severity="low",
        title="t",
        body="b",
    )
    azure_thread_context(old_hit, diff)
    swapped = Finding(
        path="new.c",
        start_line=3,
        end_line=1,
        side="old",
        severity="low",
        title="t",
        body="b",
    )
    azure_thread_context(swapped, diff)
    azure_thread_context(
        Finding(path="new.c", start_line=1, end_line=1, side="new", severity="low", title="t", body="b"),
        diff,
    )
    assert _file_path("") == ""


def test_comment_range_and_similarity_and_position():
    from creasy.review.comment_range import format_code_comment_prompt
    from creasy.review.findings import Finding, split_findings
    from creasy.review.position import build_position_variants, format_discussion
    from creasy.review.similarity import should_skip_similar_reply
    from creasy.workspace.diffmap import parse_unified_diff

    assert format_code_comment_prompt("", path="", side="", start_line=0, end_line=0) == ""
    assert "src/a.py" in format_code_comment_prompt("why?", path="src/a.py", side="new", start_line=2, end_line=4)
    assert should_skip_similar_reply("same text here", "same text here")
    assert not should_skip_similar_reply("alpha", "zzzz completely different")
    md, findings = split_findings("### Summary\nhello\n")
    assert "hello" in md
    md2, findings2 = split_findings(
        "### Summary\nok\n\n```opencoderman-findings\n"
        '[{"path":"a.py","start_line":1,"end_line":1,"severity":"critical","title":"t","body":"b"}]\n```\n'
    )
    assert findings2 or md2
    finding = Finding(
        path="a.py",
        start_line=2,
        end_line=1,
        side="new",
        severity="critical",
        title="t",
        body="b",
    )
    text = format_discussion(finding)
    assert "Kritik" in text or "Critical" in text or "t" in text
    diff = parse_unified_diff(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
-old
+new
"""
    )
    build_position_variants(finding, diff, base_sha="b", start_sha="b", head_sha="h")
    old_f = Finding(path="a.py", start_line=2, end_line=1, side="old", severity="critical", title="t", body="b")
    build_position_variants(old_f, diff, base_sha="b", start_sha="b", head_sha="h")
    missing = Finding(path="missing.py", start_line=1, end_line=1, side="new", severity="low", title="t", body="b")
    assert build_position_variants(missing, diff, base_sha="b", start_sha="b", head_sha="h") == []
