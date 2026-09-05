from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return int(raw)


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 9001
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""
    webhook_secret: str = ""
    opencode_model: str = "opencode/big-pickle"
    opencode_timeout: int = 1800
    opencode_retry_count: int = 2
    opencode_agent: str = "gitlab-reviewer"
    opencode_bin: str = "opencode"
    max_concurrent_jobs: int = 2
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    skip_draft_mrs: bool = True
    dashboard_token: str = ""
    log_level: str = "INFO"
    git_timeout: int = 600
    serve_health_timeout: int = 60
    hang_timeout: int = 300

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def job_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def serve_dir(self) -> Path:
        return self.data_dir / "serve"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.work_dir, self.job_dir, self.log_dir, self.serve_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_config(env_file: str | None = ".env") -> Config:
    if env_file:
        load_dotenv(env_file, override=False)
    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
    cfg = Config(
        host=os.getenv("HOST", "0.0.0.0"),
        port=_int("PORT", 9001),
        gitlab_url=os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/"),
        gitlab_token=(os.getenv("GITLAB_TOKEN") or "").strip(),
        webhook_secret=(os.getenv("WEBHOOK_SECRET") or "").strip(),
        opencode_model=os.getenv("OPENCODE_MODEL", "opencode/big-pickle").strip(),
        opencode_timeout=max(1, _int("OPENCODE_TIMEOUT", 1800)),
        opencode_retry_count=max(1, _int("OPENCODE_RETRY_COUNT", 2)),
        opencode_agent=os.getenv("OPENCODE_AGENT", "gitlab-reviewer").strip() or "gitlab-reviewer",
        opencode_bin=os.getenv("OPENCODE_BIN", "opencode").strip() or "opencode",
        max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", 2)),
        data_dir=data_dir,
        skip_draft_mrs=_bool("SKIP_DRAFT_MRS", True),
        dashboard_token=(os.getenv("DASHBOARD_TOKEN") or "").strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        git_timeout=max(30, _int("GIT_TIMEOUT", 600)),
        serve_health_timeout=max(5, _int("SERVE_HEALTH_TIMEOUT", 60)),
        hang_timeout=max(30, _int("HANG_TIMEOUT", 300)),
    )
    cfg.ensure_dirs()
    return cfg
