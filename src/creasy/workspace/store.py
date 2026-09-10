from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from creasy.jobs.models import utc_now
from creasy.logging import get_logger

logger = get_logger("workspace.store")


def _record_from_dict(data: dict) -> WorkspaceRecord:
    kwargs = {}
    for item in fields(WorkspaceRecord):
        if item.name in data:
            kwargs[item.name] = data[item.name]
        elif item.default is not MISSING:
            kwargs[item.name] = item.default
        elif item.default_factory is not MISSING:  # type: ignore[misc]
            kwargs[item.name] = item.default_factory()
    return WorkspaceRecord(**kwargs)


@dataclass
class WorkspaceRecord:
    mr_key: str
    project_id: int
    mr_iid: int
    clone_path: str = ""
    source_branch: str = ""
    target_branch: str = ""
    last_sha: str = ""
    session_id: str = ""
    last_job_id: str = ""
    http_url: str = ""
    web_url: str = ""
    updated_at: str = ""


class WorkspaceStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, mr_key: str) -> Path:
        return self.root / f"{mr_key}.json"

    def get(self, mr_key: str) -> Optional[WorkspaceRecord]:
        path = self._path(mr_key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("corrupt workspace meta path=%s", path)
            return None
        if not isinstance(data, dict):
            logger.warning("corrupt workspace meta path=%s", path)
            return None
        return _record_from_dict(data)

    def save(self, record: WorkspaceRecord) -> WorkspaceRecord:
        record.updated_at = utc_now()
        path = self._path(record.mr_key)
        tmp = path.with_name(f"{record.mr_key}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(asdict(record), indent=2)
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
        return record

    def delete(self, mr_key: str) -> None:
        path = self._path(mr_key)
        if path.is_file():
            path.unlink()

    def list_all(self) -> list[WorkspaceRecord]:
        out: list[WorkspaceRecord] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                out.append(_record_from_dict(data))
            except Exception:
                continue
        return [r for r in out if r.mr_key]
