"""Executable zip layout. Does not run PyInstaller."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "packaging" / "build_exe.py"
    spec = importlib.util.spec_from_file_location("creasy_build_exe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zip_contains_only_exe_and_config(tmp_path: Path) -> None:
    exe = tmp_path / "creasy.exe"
    cfg = tmp_path / ".env.example"
    exe.write_bytes(b"exe")
    cfg.write_text("PORT=9001\n", encoding="utf-8")
    dest = tmp_path / "creasy-0.3.0-windows-x64.zip"
    mod = _load()
    mod.write_exe_zip(exe, cfg, dest)
    mod.assert_exe_zip(dest, expect_exe="creasy.exe")
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == [".env.example", "creasy.exe"]


def test_zip_name() -> None:
    assert _load().zip_name("0.3.0", "linux-x64") == "creasy-0.3.0-linux-x64.zip"


def test_assert_rejects_extra_files(tmp_path: Path) -> None:
    dest = tmp_path / "bad.zip"
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("creasy", "x")
        zf.writestr(".env.example", "y")
        zf.writestr("README.txt", "no")
    try:
        _load().assert_exe_zip(dest, expect_exe="creasy")
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")
