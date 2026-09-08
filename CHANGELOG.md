# Changelog

Operator-facing notes for each released version. Heading form is
`## X.Y.Z — YYYY-MM-DD`. `scripts/release_notes.py` copies that
section onto the GitHub Release — write what changed and why, not a
file list. Keep an `## Unreleased` section for work that is not
versioned yet. Bump `VERSION` and move Unreleased into a dated
section in the same change.

## Unreleased

### Fixed

- Azure PR fetch no longer 404s when `AZURE_DEVOPS_URL` is only the
  TFS host. The collection (`/tfs/ExampleCollection`) is taken from
  the Service Hook or the PR URL, and a collection-scoped git path is
  tried after a project-GUID path returns 404.
- Azure DevOps Server PAT auth uses Basic `pat:<PAT>` (IIS rejects the
  cloud-style empty username). Clone URLs stay under `/tfs/<Collection>/…/_git/…`
  and are not the REST `_apis` URL. Git also sends the same Basic header.

## 0.5.0 — 2026-09-08

Optional Azure DevOps Server reviews on the same Creasy process.
GitLab `/webhook` is unchanged. The README now has the click-path
for creating both webhooks.

### Added

- Optional Azure DevOps Server 2022.2 provider. Service Hooks post to
  `/webhook/azure`. Auto review is PR created plus `/review` `/ask`
  `/reset`. Overview and file threads use the 7.1 threads API.
  Comment hooks resolve the PR from links if Azure omits
  `resource.pullRequest`. File threads send iteration context.
  `/reset` and similar-match paginate threads. Clone URL is built as
  HTTPS when `remoteUrl` is SSH. Azure Basic auth is
  `AZURE_WEBHOOK_PASSWORD` only (not `WEBHOOK_SECRET`).
- README webhook setup for GitLab (MR events + comments,
  `X-Gitlab-Token`) and Azure Service Hooks (created, commented,
  updated, merge attempted, Basic auth).

### Changed

- Every webhook, HTTP, git, OpenCode, and job step now logs `ok` or
  `FAIL` so a dual GitLab+Azure install can be grepped end to end.

## 0.4.0 — 2026-09-06

Auto review no longer follows every push. Opening an MR still starts
one job; later commits, reopen, and mark-as-ready stay quiet until
someone comments `/review`. Commands and the jobs search are less
fussy in daily use.

### Changed

- Auto review runs only when an MR is opened. New commits, reopen,
  and mark-as-ready no longer start a job. Comment `/review` to
  review again.

### Fixed

- `/review.`, `/ask?`, and `/reset!` now run. Trailing punctuation
  used to make the command look like it did nothing.
- Editing a `/review` or `/reset` comment no longer starts a second
  job. GitLab 16.11+ refires the Note Hook with `action=update`.
- Jobs search matches `!30`, the iid, and the MR title, not only the
  `project-iid` key.

## 0.3.0 — 2026-09-06

Operator download on the GitHub Release is three executable zips
(Windows, Linux, Apple Silicon). Each zip is one `creasy` /
`creasy.exe` plus `.env.example`. Intel macOS is not built:
GitHub no longer assigns `macos-13` runners, which blocked the
0.3.0 publish job. The dashboard brand line shows the product
version.

### Added

- One-file executables built in CI (`packaging/build_exe.py`) and
  uploaded as artifacts. Release notes list only those zips.
- Sidebar brand shows `Creasy vX.Y.Z` at the top left.

### Fixed

- A trailing wrap-up on a resumed session is no longer posted as the
  Overview note. The review from this turn is used, so findings still
  become diff threads.
- Inline threads still post when GitLab omits `diff_refs`; positions
  fall back to the live merge-base.
- Marking a draft ready now enqueues a review (update without oldrev
  but `changes.draft` true → false).
- Jobs list has a Refresh button and polls while the live socket is
  down, so the page does not stay stale.

## 0.2.0 — 2026-09-06

First versioned release. `VERSION` is the single product version
(`/health`, `/api/meta`, offline packs, and GitLab notes).

### Added

- `/reset` on an MR deletes that MR’s notes and threads authored by
  the `GITLAB_TOKEN` user and clears `ses_*`. No OpenCode.
- Dashboard token: SPA sends `X-Creasy-Token`; `/ws` requires it when
  `DASHBOARD_TOKEN` is set. Sidebar token field and `?token=`.
- Sidebar shows the running product version.
- `python scripts/bump_version.py patch|minor|major`.
- `develop` is the daily branch. `main` is release-only.

### Fixed

- A hung follow-up no longer republishes the previous review as success.
- Cancel/close can stop an in-flight `git` clone or fetch.
- Close/merge cannot start a new job that recreates the clone mid-delete.
- Notes are ignored until the token user id is known (no self-trigger).
- Workspace meta is written atomically; corrupt JSON does not crash the
  next job.
- A job whose Overview note never posts is `error`, not `success`.
- Serve health fails if the `opencode serve` child has already exited.

### Packs

GitHub Release assets (not the “Source code” zip) include bundled
CPython, wheels, `opencode` / `opencode.exe`, `rg` / `rg.exe`, and
`web/dist`. Unzip a pack, then `install`, `install-opencode`, `start`.
