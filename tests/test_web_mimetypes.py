"""SPA MIME overrides (Windows .js → text/plain fix)."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api import dashboard
from creasy.api.web_mimetypes import ensure_spa_mimetypes, media_type_for_path


def test_ensure_spa_mimetypes_overrides_text_plain_js() -> None:
    mimetypes.init()
    mimetypes.types_map[".js"] = "text/plain"
    ensure_spa_mimetypes()
    assert mimetypes.types_map[".js"] == "text/javascript"
    assert "javascript" in (media_type_for_path(Path("assets/index-abc.js")) or "")


def test_media_type_for_css_and_wasm() -> None:
    ensure_spa_mimetypes()
    assert media_type_for_path("x.css") == "text/css"
    assert media_type_for_path("x.wasm") == "application/wasm"
    assert media_type_for_path("x.html") == "text/html"


def test_js_assets_not_served_as_text_plain(tmp_path: Path, monkeypatch) -> None:
    """Windows registry can map .js → text/plain; SPA module scripts require JS MIME."""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>Creasy</title></head><body></body></html>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    (assets / "app.css").write_text("body{}", encoding="utf-8")

    mimetypes.init()
    mimetypes.types_map[".js"] = "text/plain"
    mimetypes.types_map[".css"] = "text/plain"

    monkeypatch.setattr(dashboard, "spa_dir", lambda: dist)
    app = FastAPI()
    dashboard.attach_spa(app)
    client = TestClient(app)

    js = client.get("/assets/app.js")
    assert js.status_code == 200
    js_ct = (js.headers.get("content-type") or "").lower()
    assert "javascript" in js_ct, f"expected JS MIME, got {js_ct!r}"
    assert "text/plain" not in js_ct

    css = client.get("/assets/app.css")
    css_ct = (css.headers.get("content-type") or "").lower()
    assert "text/css" in css_ct, f"expected CSS MIME, got {css_ct!r}"

    html = client.get("/jobs")
    html_ct = (html.headers.get("content-type") or "").lower()
    assert "html" in html_ct, f"expected HTML MIME, got {html_ct!r}"
