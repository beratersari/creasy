from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from creasy.jobs.models import LIVE_STATUSES, JobRecord, utc_now


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def get(self, job_id: str) -> Optional[JobRecord]:
        path = self._path(job_id)
        with self._lock:
            if not path.is_file():
                return None
            raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        try:
            return JobRecord.model_validate_json(raw)
        except Exception:
            return None

    def save(self, job: JobRecord) -> JobRecord:
        job.updated_at = utc_now()
        path = self._path(job.job_id)
        tmp = path.with_name(f"{job.job_id}.{uuid.uuid4().hex}.tmp")
        payload = job.model_dump_json(indent=2)
        with self._lock:
            tmp.write_text(payload, encoding="utf-8")
            last: Exception | None = None
            for _ in range(8):
                try:
                    os.replace(tmp, path)
                    last = None
                    break
                except OSError as exc:
                    last = exc
                    time.sleep(0.05)
            if last is not None:
                try:
                    path.write_text(payload, encoding="utf-8")
                    tmp.unlink(missing_ok=True)
                except OSError as exc:
                    raise last from exc
        return job

    def list_all(self) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for path in self.root.glob("job_*.json"):
            try:
                jobs.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        jobs.sort(key=lambda j: j.accepted_at or "", reverse=True)
        return jobs

    def live_for_mr(self, mr_key: str) -> list[JobRecord]:
        return [j for j in self.list_all() if j.mr_key == mr_key and j.status in LIVE_STATUSES]

    def running_for_mr(self, mr_key: str) -> Optional[JobRecord]:
        for job in self.list_all():
            if job.mr_key == mr_key and job.status == "running":
                return job
        return None
