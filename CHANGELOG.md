# Changelog

Operator-facing notes for each released version. Heading form is
`## X.Y.Z — YYYY-MM-DD`. `scripts/release_notes.py` copies that
section onto the GitHub Release — write what changed and why, not a
file list. Keep an `## Unreleased` section for work that is not
versioned yet. Bump `VERSION` and move Unreleased into a dated
section in the same change.

## Unreleased

## 0.3.0 — 2026-09-06

Operator download on the GitHub Release is four executable zips.
Each zip is one `creasy` / `creasy.exe` plus `.env.example`.
The dashboard brand line shows the product version.

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
