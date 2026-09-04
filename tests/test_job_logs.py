"""Job logs are tagged and filtered by job_id like OSM / Virtual Developer."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.log_context import bound
from creasy.logging import get_logger, read_job_log_lines, setup_logging
from conftest import FakeRunner


def test_read_job_log_lines_filters_by_job_id(tmp_config):
    setup_logging("INFO", tmp_config.log_dir)
    log = get_logger("worker")
    first = mint_job_id()
    second = mint_job_id()
    with bound(first, "1-1", f"1-1-{first}.log"):
        log.info("clone started for first")
    with bound(second, "1-2", f"1-2-{second}.log"):
        log.info("clone started for second")
    log.info("unbound line must not appear")

    a = read_job_log_lines(tmp_config.log_dir, first, mr_key="1-1", log_file=f"1-1-{first}.log")
    b = read_job_log_lines(tmp_config.log_dir, second, mr_key="1-2", log_file=f"1-2-{second}.log")
    a_text = "\n".join(row["message"] for row in a)
    b_text = "\n".join(row["message"] for row in b)
    assert "clone started for first" in a_text
    assert f"job_id={first}" in a_text
    assert "clone started for second" not in a_text
    assert "unbound line must not appear" not in a_text
    assert "clone started for second" in b_text
    assert "clone started for first" not in b_text
    assert all(row["job_id"] == first for row in a)


def test_dashboard_logs_endpoint_returns_only_that_job(tmp_config):
    setup_logging("INFO", tmp_config.log_dir)
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="9-9",
        project_id=9,
        mr_iid=9,
        trigger="review",
        status="success",
        live=False,
        log_file="",
    )
    job.log_file = f"{job.mr_key}-{job.job_id}.log"
    manager.store.save(job)
    with bound(job.job_id, job.mr_key, job.log_file):
        get_logger("worker").info("pipeline start for dashboard test")

    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    client = TestClient(app)
    body = client.get(f"/api/jobs/{job.job_id}/logs").json()
    messages = [row["message"] for row in body["lines"]]
    assert any("pipeline start for dashboard test" in line for line in messages)
    assert all(row["job_id"] == job.job_id for row in body["lines"])
    manager.shutdown()
