"""Offline packager helpers (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "packaging" / "build_dist.py"
    spec = importlib.util.spec_from_file_location("creasy_build_dist", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_read_versions(tmp_path: Path) -> None:
    p = tmp_path / "versions.env"
    p.write_text(
        "# comment\nOPENCODE_VERSION=1.18.10\nPYTHON_MIN_VERSION=3.11\n",
        encoding="utf-8",
    )
    data = _load().read_versions(p)
    assert data["OPENCODE_VERSION"] == "1.18.10"
    assert data.get("PYTHON_MIN_VERSION") == "3.11"


def test_runtime_requirements_from_pyproject() -> None:
    root = Path(__file__).resolve().parents[1]
    deps = _load().runtime_requirements(root)
    joined = " ".join(deps).lower()
    assert "fastapi" in joined
    assert "uvicorn" in joined
    assert "httpx" in joined


def test_standalone_python_asset_names() -> None:
    ver = {
        "PYTHON_FULL_VERSION": "3.12.14",
        "PYTHON_STANDALONE_TAG": "20260825",
    }
    mod = _load()
    win = mod.standalone_python_asset(ver, "windows")
    linux = mod.standalone_python_asset(ver, "linux")
    dar = mod.standalone_python_asset(ver, "darwin-arm64")
    assert win.endswith("x86_64-pc-windows-msvc-install_only.tar.gz")
    assert linux.endswith("x86_64-unknown-linux-gnu-install_only.tar.gz")
    assert dar.endswith("aarch64-apple-darwin-install_only.tar.gz")
    assert "3.12.14" in win and "3.12.14" in linux


def test_opencode_asset_names() -> None:
    ver = {
        "OPENCODE_WINDOWS_ASSET": "opencode-windows-x64.zip",
        "OPENCODE_LINUX_ASSET": "opencode-linux-x64.tar.gz",
        "OPENCODE_DARWIN_ARM64_ASSET": "opencode-darwin-arm64.zip",
    }
    mod = _load()
    assert mod.opencode_asset(ver, "windows", "x64").endswith(".zip")
    assert mod.opencode_asset(ver, "linux", "x64").endswith(".tar.gz")
    assert "arm64" in mod.opencode_asset(ver, "darwin", "arm64")


def test_copy_dirs_include_opencode_configs() -> None:
    mod = _load()
    assert "opencoderman" in mod.COPY_DIRS
    assert "agents" not in mod.SKIP_DIR_NAMES
    assert ".env.example" in mod.COPY_FILES


def test_stage_app_copies_env_example(tmp_path: Path) -> None:
    """install.sh / start.sh seed .env from this file after unpacking the CI zip."""
    mod = _load()
    root = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    mod.stage_app(root, payload)
    packed = payload / ".env.example"
    assert packed.is_file()
    text = packed.read_text(encoding="utf-8")
    assert "GITLAB_TOKEN" in text
    assert "WEBHOOK_SECRET" in text


def test_stage_app_copies_opencode_configs(tmp_path: Path) -> None:
    mod = _load()
    root = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    mod.stage_app(root, payload)
    packed = payload / "opencoderman" / "agents" / "gitlab-reviewer.md"
    assert packed.is_file()
    assert "mode: primary" in packed.read_text(encoding="utf-8")
    assert not (payload / "opencoderman" / ".git").exists()


def test_stage_app_requires_review_agent(tmp_path: Path) -> None:
    mod = _load()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("x", encoding="utf-8")
    payload = tmp_path / "payload"
    try:
        mod.stage_app(root, payload)
    except SystemExit as exc:
        assert "opencoderman/agents/gitlab-reviewer.md" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_four_platform_packs() -> None:
    mod = _load()
    assert set(mod.PACKS) == {"windows", "linux", "darwin", "winlinux"}
    assert mod.PACKS["windows"]["suffix"] == "windows-x64"
    assert mod.PACKS["linux"]["suffix"] == "linux-x64"
    assert mod.PACKS["darwin"]["suffix"] == "darwin"
    assert mod.PACKS["winlinux"]["suffix"] == "windows-linux"


def test_build_dist_default_does_not_zip() -> None:
    """CI uploads folders; Actions wraps them. --zip is opt-in for local copies."""
    text = Path(__file__).resolve().parents[1].joinpath("packaging", "build_dist.py").read_text(
        encoding="utf-8"
    )
    assert '"--zip"' in text
    assert "if args.zip:" in text
    workflow = Path(__file__).resolve().parents[1].joinpath(".github", "workflows", "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "--zip" not in workflow
    assert "dist/creasy-*.zip" not in workflow


def test_install_requires_bundled_python() -> None:
    bat = Path(__file__).resolve().parents[1] / "scripts" / "install.bat"
    sh = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
    assert "vendor\\python\\windows\\python.exe" in bat.read_text(encoding="utf-8")
    assert "creasy_require_bundled_python" in sh.read_text(encoding="utf-8")
    assert "build_dist.py" in bat.read_text(encoding="utf-8")


def test_copy_web_requires_built_spa(tmp_path: Path) -> None:
    mod = _load()
    root = tmp_path / "repo"
    (root / "web" / "dist").mkdir(parents=True)
    payload = tmp_path / "payload"
    try:
        mod.copy_web(root, payload)
    except SystemExit as exc:
        assert "web/dist/index.html" in str(exc)
    else:
        raise AssertionError("copy_web must fail without a built SPA")
    (root / "web" / "dist" / "index.html").write_text(
        '<title>Creasy</title><script src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (root / "web" / "dist" / "assets").mkdir()
    (root / "web" / "dist" / "assets" / "app.js").write_text("1", encoding="utf-8")
    mod.copy_web(root, payload)
    shipped = payload / "web" / "dist" / "index.html"
    assert shipped.is_file()
    assert "Creasy" in shipped.read_text(encoding="utf-8")
    assert (payload / "web" / "src").exists() is False


def test_ensure_web_dist_requires_index_without_npm(tmp_path: Path) -> None:
    mod = _load()
    root = tmp_path / "repo"
    (root / "web" / "dist").mkdir(parents=True)
    try:
        mod.ensure_web_dist(root, use_npm=False)
    except SystemExit as exc:
        assert "web/dist/index.html" in str(exc)
    else:
        raise AssertionError("ensure_web_dist must fail without a built SPA")
    (root / "web" / "dist" / "index.html").write_text("<title>Creasy</title>", encoding="utf-8")
    mod.ensure_web_dist(root, use_npm=False)
