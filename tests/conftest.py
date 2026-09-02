from __future__ import annotations

import threading
from pathlib import Path

import pytest

from creasy.config import Config
from creasy.jobs.worker import RunResult


class FakeRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.runs: list[str] = []
        self.current: str = ""

    def run(self, job, should_stop):
        self.current = job.job_id
        self.runs.append(job.trigger + ":" + (job.comment_text or ""))
        self.started.set()
        while not self.release.wait(0.05):
            if should_stop():
                return RunResult(cancelled=True, error="cancelled")
        self.release.clear()
        return RunResult(text="ok " + job.trigger, session_id="ses_test")


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    cfg = Config(
        data_dir=tmp_path / "data",
        webhook_secret="secret",
        gitlab_token="token",
        max_concurrent_jobs=2,
        skip_draft_mrs=True,
    )
    cfg.ensure_dirs()
    return cfg
