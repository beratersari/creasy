"""Dashboard uses the OSM jobs workbench frontend."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_osm_shell_branded_creasy() -> None:
    shell = (ROOT / "web" / "src" / "app" / "Shell.tsx").read_text(encoding="utf-8")
    assert "Creasy" in shell
    assert 'vd-mark">CR' in shell
    assert "Jobs" in shell


def test_jobs_page_keeps_osm_workbench() -> None:
    page = (ROOT / "web" / "src" / "pages" / "jobs" / "JobsPage.tsx").read_text(encoding="utf-8")
    assert "Reviews" in page
    assert "Workbench" not in page
    assert "vd-job" in page
    assert "Find merge request" in page


def test_job_detail_omits_unused_osm_fields() -> None:
    detail = (ROOT / "web" / "src" / "pages" / "jobs" / "JobDetailPage.tsx").read_text(
        encoding="utf-8"
    )
    assert 'label="Attempt"' not in detail
    assert 'label="Timeout"' not in detail
    assert 'label="Callback"' not in detail
    assert 'label="Job id"' not in detail
    assert 'label="Merge request"' not in detail
    assert "job.attempts" not in detail
    assert "timeout_in_seconds" not in detail
    assert "callback_status_code" not in detail
    assert "No OSM" not in detail


def test_report_issue_lives_only_in_the_sidebar() -> None:
    """One control. Job pages pre-select via the URL; do not add a second button."""
    shell = (ROOT / "web" / "src" / "app" / "Shell.tsx").read_text(encoding="utf-8")
    jobs = (ROOT / "web" / "src" / "pages" / "jobs" / "JobsPage.tsx").read_text(encoding="utf-8")
    detail = (ROOT / "web" / "src" / "pages" / "jobs" / "JobDetailPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "ReportIssue" in shell
    assert "ReportIssue" not in jobs
    assert "ReportIssue" not in detail
    assert "Report issue" not in detail
    assert not (ROOT / "web" / "src" / "ui" / "ReportIssueDialog.tsx").is_file()


def test_dashboard_does_not_start_reviews() -> None:
    client = (ROOT / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "POST /jobs" not in client
    assert "/webhook" not in client


def test_vite_source_html_is_not_the_served_dashboard() -> None:
    src = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "/src/main.tsx" in src
    from creasy.api.dashboard import spa_dir

    assert spa_dir() == ROOT / "web" / "dist"
    dist = spa_dir() / "index.html"
    if dist.is_file():
        built = dist.read_text(encoding="utf-8")
        assert "Creasy" in built
        assert "/assets/" in built
        assert "/src/main.tsx" not in built
