from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from creasy.jobs.models import utc_now


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

    def _path(self, mr_key: str) -> Path:
        return self.root / f"{mr_key}.json"

    def get(self, mr_key: str) -> Optional[WorkspaceRecord]:
        path = self._path(mr_key)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        fields = WorkspaceRecord.__dataclass_fields__
        return WorkspaceRecord(**{k: data[k] if k in data else fields[k].default for k in fields})

    def save(self, record: WorkspaceRecord) -> WorkspaceRecord:
        record.updated_at = utc_now()
        path = self._path(record.mr_key)
        path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
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
                fields = WorkspaceRecord.__dataclass_fields__
                out.append(WorkspaceRecord(**{k: data[k] if k in data else fields[k].default for k in fields}))
            except Exception:
                continue
        return [r for r in out if r.mr_key]
