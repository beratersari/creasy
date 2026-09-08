from creasy.diag import merge_job_diag, safe_fields, safe_url


def test_safe_url_strips_userinfo_and_query() -> None:
    assert (
        safe_url("https://pat:super-secret@tfs.example/tfs/Col/Proj/_git/Repo?token=abc")
        == "https://tfs.example/tfs/Col/Proj/_git/Repo"
    )


def test_safe_fields_never_keep_raw_tokens() -> None:
    got = safe_fields(
        {
            "token": "super-secret-pat",
            "azure_token": "also-secret",
            "token_chars": 16,
            "token_set": True,
            "clone_url": "https://pat:super-secret-pat@tfs.example/tfs/Col/_git/R",
            "stage": "clone",
        }
    )
    assert got["token_set"] is True
    assert got["azure_token_set"] is True
    assert "token" not in got or got.get("token") != "super-secret-pat"
    assert "super-secret-pat" not in str(got)
    assert got["token_chars"] == 16
    assert got["clone_url"] == "https://tfs.example/tfs/Col/_git/R"


def test_merge_job_diag_updates_record() -> None:
    class Job:
        diagnostics = {}

    job = Job()
    merge_job_diag(job, stage="git_fail", token="nope-secret", error="boom")
    assert job.diagnostics["stage"] == "git_fail"
    assert job.diagnostics["error"] == "boom"
    assert "nope-secret" not in str(job.diagnostics)
