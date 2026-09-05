from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from creasy.logging import get_logger, redact_userinfo

logger = get_logger("gitops")


class GitError(RuntimeError):
    pass


@dataclass
class DiffIndex:
    merge_base: str
    stat: str
    paths: list[str]
    statuses: dict[str, str]


def isolated_git_env(token: str = "") -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GCM_INTERACTIVE"] = "never"
    env["GCM_MODAL_PROMPT"] = "false"
    env["GCM_GUI_PROMPT"] = "false"
    # INTENTIONAL: on-prem / TLS intercept. Same policy as httpx verify=False.
    env["GIT_SSL_NO_VERIFY"] = "1"
    if token:
        env["CREASY_GIT_TOKEN"] = token
    return env


def inject_token(url: str, token: str) -> str:
    if not token:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise GitError("repo_url must be http(s)")
    host = parsed.hostname or ""
    if not host:
        raise GitError("repo_url has no host")
    netloc = f"oauth2:{token}@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def public_git_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.username and not parsed.password:
        return url
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


def _run_git(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "http.sslVerify=false",
        "-c",
        "core.longpaths=true",
        *args,
    ]
    logger.info("git %s cwd=%s", redact_userinfo(" ".join(str(a) for a in args)), cwd or ".")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env or isolated_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git timed out after {timeout}s") from exc
    except OSError as exc:
        raise GitError(f"git failed to start: {redact_userinfo(str(exc))}") from exc
    if result.returncode != 0:
        err = redact_userinfo((result.stderr or result.stdout or "").strip())
        raise GitError(f"git failed ({result.returncode}): {err[-800:]}")
    return result


def clone_repo(repo_url: str, dest: Path, token: str, *, timeout: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise GitError(f"clone dest already exists: {dest}")
    env = isolated_git_env(token)
    auth_url = inject_token(repo_url, token)
    try:
        _run_git(["clone", "--no-single-branch", auth_url, str(dest)], env=env, timeout=timeout)
        _scrub_origin(dest, env, timeout=min(30.0, timeout))
    except Exception:
        if dest.exists():
            delete_clone(dest)
        raise


def _scrub_origin(dest: Path, env: dict[str, str], timeout: float) -> None:
    result = _run_git(
        ["config", "--local", "--get", "remote.origin.url"],
        cwd=dest,
        env=env,
        timeout=timeout,
    )
    url = (result.stdout or "").strip()
    if urlparse(url).username or urlparse(url).password:
        clean = public_git_url(url)
        _run_git(["remote", "set-url", "origin", clean], cwd=dest, env=env, timeout=timeout)


def fetch_and_checkout(
    dest: Path,
    *,
    source_branch: str,
    target_branch: str,
    sha: str,
    token: str,
    timeout: float,
) -> str:
    env = isolated_git_env(token)
    origin = _origin_url(dest, env, timeout=min(30.0, timeout))
    auth = inject_token(origin, token) if token else origin
    if token and auth != origin:
        _run_git(["remote", "set-url", "origin", auth], cwd=dest, env=env, timeout=min(30.0, timeout))
    try:
        refs = [b for b in (source_branch, target_branch) if b]
        fetch_args = ["fetch", "--force", "origin", *refs]
        _run_git(fetch_args, cwd=dest, env=env, timeout=timeout)
        target = sha or f"origin/{source_branch}"
        _run_git(["checkout", "--force", "--detach", target], cwd=dest, env=env, timeout=min(60.0, timeout))
        _run_git(["reset", "--hard", "HEAD"], cwd=dest, env=env, timeout=min(60.0, timeout))
        _run_git(["clean", "-fd"], cwd=dest, env=env, timeout=min(60.0, timeout))
        head = _run_git(["rev-parse", "HEAD"], cwd=dest, env=env, timeout=30)
        return (head.stdout or "").strip()
    finally:
        if token:
            try:
                _scrub_origin(dest, env, timeout=min(30.0, timeout))
            except Exception:
                logger.warning("could not scrub origin after fetch")


def _origin_url(dest: Path, env: dict[str, str], timeout: float) -> str:
    result = _run_git(
        ["config", "--local", "--get", "remote.origin.url"],
        cwd=dest,
        env=env,
        timeout=timeout,
    )
    return (result.stdout or "").strip()


def resolve_merge_base(
    dest: Path,
    *,
    target_branch: str,
    preferred_base: str = "",
    timeout: float = 60,
) -> str:
    """Separation point = current merge-base of target and HEAD.

    After a rebase onto a moved target, that is the new target tip — not the
    original fork commit. GitLab ``base_sha`` is only a fallback: it can be
    stale right after a force-push, and if we used the old SHA the three-dot
    diff would include target-branch commits that are not part of the MR.
    """
    env = isolated_git_env()
    target_ref = f"origin/{target_branch}" if target_branch else "origin/HEAD"
    try:
        computed = (
            _run_git(["merge-base", target_ref, "HEAD"], cwd=dest, env=env, timeout=timeout).stdout or ""
        ).strip()
    except GitError:
        computed = ""
    if computed:
        if preferred_base and preferred_base != computed:
            logger.info(
                "ignoring GitLab base_sha=%s; using merge-base(%s, HEAD)=%s",
                preferred_base,
                target_ref,
                computed,
            )
        return computed
    if preferred_base:
        try:
            _run_git(
                ["cat-file", "-e", f"{preferred_base}^{{commit}}"],
                cwd=dest,
                env=env,
                timeout=timeout,
            )
            logger.warning("merge-base failed; falling back to GitLab base_sha %s", preferred_base)
            return preferred_base
        except GitError:
            pass
    raise GitError(f"could not resolve merge-base against {target_ref}")


def diff_stat(dest: Path, merge_base: str, *, timeout: float = 60) -> DiffIndex:
    env = isolated_git_env()
    stat = _run_git(
        ["diff", "--stat", f"{merge_base}...HEAD"],
        cwd=dest,
        env=env,
        timeout=timeout,
    )
    name_status = _run_git(
        ["diff", "--name-status", f"{merge_base}...HEAD"],
        cwd=dest,
        env=env,
        timeout=timeout,
    )
    paths: list[str] = []
    statuses: dict[str, str] = {}
    for line in (name_status.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        path = parts[-1].strip()
        if not path:
            continue
        paths.append(path)
        statuses[path] = status
    return DiffIndex(merge_base=merge_base, stat=(stat.stdout or "").strip(), paths=paths, statuses=statuses)


def unified_diff(dest: Path, merge_base: str, *, timeout: float = 60) -> str:
    env = isolated_git_env()
    result = _run_git(
        ["diff", "--find-renames", f"{merge_base}...HEAD"],
        cwd=dest,
        env=env,
        timeout=timeout,
    )
    return result.stdout or ""


def delete_clone(path: Optional[Path], retries: int = 8) -> None:
    """OSM hard-delete cascade. ``retries`` is ignored; OSM uses its own attempt budget."""
    from creasy.cleanup.end import delete_clone_path

    dest = None if path is None else Path(path)
    ok = delete_clone_path(dest, reason="delete_clone")
    if dest is not None:
        try:
            still = dest.exists()
        except OSError:
            still = True
        if still and not ok:
            raise GitError(f"could not remove clone at {dest}")
