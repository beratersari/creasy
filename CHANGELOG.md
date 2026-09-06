# Changelog

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
