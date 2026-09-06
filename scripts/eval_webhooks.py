"""Send N real /review webhooks at a running Creasy and score outcomes.

Uses the real GitLab project from tester/payloads.py. Writes JSONL plus
a summary under --out (default data/eval-200).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tester.payloads import (
    DEFAULT_MR_IID,
    DEFAULT_PROJECT_ID,
    DEFAULT_SOURCE,
    DEFAULT_TARGET,
    DEFAULT_WEB_URL,
    build_payload,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None, timeout: float = 30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"text": raw}
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw) if raw else {"detail": raw}
        except json.JSONDecodeError:
            body = {"detail": raw}
        return exc.code, body


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def send_webhooks(args) -> list[dict]:
    acks: list[dict] = []
    url = args.base.rstrip("/") + "/webhook"
    headers = {"Content-Type": "application/json"}
    if args.secret:
        headers["X-Gitlab-Token"] = args.secret
    for index in range(1, args.count + 1):
        payload = build_payload(
            "review",
            project_id=args.project_id,
            mr_iid=args.mr_iid,
            source_branch=args.source,
            target_branch=args.target,
            note=f"/review eval-{args.tag} {index}/{args.count}",
            web_url=DEFAULT_WEB_URL,
        )
        started = time.time()
        try:
            status, body = _http("POST", url, headers=headers, data=json.dumps(payload).encode())
            err = ""
        except Exception as exc:  # noqa: BLE001
            status, body, err = 0, {}, str(exc)
        row = {
            "n": index,
            "at": _now(),
            "ms": int((time.time() - started) * 1000),
            "http": status,
            "ack": body.get("status") if isinstance(body, dict) else None,
            "job_id": body.get("job_id") if isinstance(body, dict) else None,
            "message": body.get("message") if isinstance(body, dict) else None,
            "error": err,
        }
        acks.append(row)
        _append(args.out / "webhook-acks.jsonl", row)
        if index % 25 == 0 or index == args.count:
            print(f"sent {index}/{args.count} last_http={status} last_ack={row['ack']}", flush=True)
    return acks


def _jobs(args) -> list[dict]:
    status, body = _http("GET", args.base.rstrip("/") + "/api/jobs?page=1&page_size=100&filter=all")
    if status != 200:
        return []
    jobs = list(body.get("jobs") or [])
    total = int(body.get("total") or len(jobs))
    page = 2
    while len(jobs) < total:
        status, body = _http(
            "GET",
            args.base.rstrip("/") + f"/api/jobs?page={page}&page_size=100&filter=all",
        )
        if status != 200:
            break
        batch = body.get("jobs") or []
        if not batch:
            break
        jobs.extend(batch)
        page += 1
    return jobs


def wait_jobs(args, wanted: set[str]) -> dict:
    deadline = time.time() + args.wait
    last = {}
    while time.time() < deadline:
        jobs = [j for j in _jobs(args) if j.get("job_id") in wanted]
        by_status: dict[str, int] = {}
        for job in jobs:
            st = str(job.get("status") or "?")
            by_status[st] = by_status.get(st, 0) + 1
        last = {
            "seen": len(jobs),
            "wanted": len(wanted),
            "by_status": by_status,
            "at": _now(),
        }
        live = by_status.get("queued", 0) + by_status.get("running", 0)
        (args.out / "progress.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
        if jobs and live == 0 and len(jobs) >= len(wanted):
            break
        time.sleep(args.poll)
    return last


def score(args, acks: list[dict], progress: dict) -> dict:
    wanted = {row["job_id"] for row in acks if row.get("job_id")}
    jobs = [j for j in _jobs(args) if j.get("job_id") in wanted]
    http_ok = sum(1 for row in acks if row.get("http") == 200)
    accepted = sum(1 for row in acks if row.get("ack") in {"accepted", "queued"})
    ignored = sum(1 for row in acks if row.get("ack") == "ignored")
    http_fail = len(acks) - http_ok
    by_status: dict[str, int] = {}
    wrapups = 0
    empty = 0
    no_findings_shape = 0
    for job in jobs:
        st = str(job.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1
        text = job.get("text") or ""
        if not text.strip():
            empty += 1
        if "review is complete" in text.lower() and "### Summary" not in text:
            wrapups += 1
        if st == "success" and "### Summary" not in text and "#### " not in text:
            no_findings_shape += 1
    finished = sum(by_status.get(s, 0) for s in ("success", "error", "timeout", "cancelled"))
    failed = by_status.get("error", 0) + by_status.get("timeout", 0)
    summary = {
        "at": _now(),
        "webhooks": len(acks),
        "http_ok": http_ok,
        "http_fail": http_fail,
        "accepted_or_queued": accepted,
        "ignored": ignored,
        "jobs_seen": len(jobs),
        "jobs_finished": finished,
        "by_status": by_status,
        "job_fail": failed,
        "job_fail_rate": (failed / finished) if finished else None,
        "webhook_fail_rate": (http_fail / len(acks)) if acks else None,
        "wrapup_notes": wrapups,
        "empty_text": empty,
        "success_without_review_shape": no_findings_shape,
        "progress": progress,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:9001")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--project-id", type=int, default=DEFAULT_PROJECT_ID)
    parser.add_argument("--mr-iid", type=int, default=DEFAULT_MR_IID)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--secret", default="")
    parser.add_argument("--out", type=Path, default=Path("data/eval-200"))
    parser.add_argument("--tag", default="200")
    parser.add_argument("--wait", type=float, default=8 * 3600)
    parser.add_argument("--poll", type=float, default=20)
    parser.add_argument("--send-only", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--wait-only", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    def _load_acks() -> list[dict]:
        path = args.out / "webhook-acks.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    if args.score_only:
        print(json.dumps(score(args, _load_acks(), {}), indent=2))
        return
    if args.wait_only:
        acks = _load_acks()
        wanted = {row["job_id"] for row in acks if row.get("job_id")}
        progress = wait_jobs(args, wanted)
        print(json.dumps(score(args, acks, progress), indent=2))
        return
    acks = send_webhooks(args)
    if args.send_only:
        print(json.dumps({"sent": len(acks), "http_ok": sum(1 for a in acks if a["http"] == 200)}, indent=2))
        return
    wanted = {row["job_id"] for row in acks if row.get("job_id")}
    progress = wait_jobs(args, wanted)
    print(json.dumps(score(args, acks, progress), indent=2))


if __name__ == "__main__":
    main()
