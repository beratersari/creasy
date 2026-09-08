"""Derive the Azure DevOps collection root from a webhook or PR URL."""

from __future__ import annotations

from urllib.parse import unquote, urlparse, urlunparse

_RESOURCE_MARKERS = ("/_git/", "/_apis/", "/pullrequest/")


def looks_like_azure_resource(url: str) -> bool:
    text = unquote(str(url or "")).lower()
    return any(marker in text for marker in _RESOURCE_MARKERS)


def normalize_collection_url(url: str) -> str:
    """Strip a project, repo, PR, or _apis suffix down to the collection root."""
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = unquote(parsed.path or "")
    lower = path.lower()
    if "/_apis/" in lower:
        path = path[: lower.index("/_apis/")]
    elif lower.endswith("/_apis"):
        path = path[: -len("/_apis")]
    lower = path.lower()
    if "/_git/" in lower:
        head = path[: lower.index("/_git/")].rstrip("/")
        if "/" in head:
            head = head.rsplit("/", 1)[0]
        path = head
    lower = path.lower()
    if "/pullrequest/" in lower:
        path = path[: lower.index("/pullrequest/")]
        path = path.rsplit("/", 1)[0] if "/" in path.rstrip("/") else path
    path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def resolve_collection_url(*, configured: str = "", collection: str = "", web_url: str = "") -> str:
    """Prefer the webhook collection, then the PR URL, then AZURE_DEVOPS_URL.

    Operators often set the server host (https://tfs02.example/ ) and omit
    /tfs/Collection. The Service Hook and PR web URL still have the collection.
    If the configured URL includes a project (_git / extra segment), prefer the
    shorter collection derived from the PR.
    """
    from_hook = normalize_collection_url(collection)
    from_web = normalize_collection_url(web_url) if looks_like_azure_resource(web_url) else ""
    from_cfg = normalize_collection_url(configured)
    derived = from_hook or from_web
    if derived and from_cfg:
        cfg = from_cfg.rstrip("/")
        got = derived.rstrip("/")
        if got.startswith(cfg + "/") or got == cfg:
            return got
        if cfg.startswith(got + "/"):
            return got
    return derived or from_cfg
