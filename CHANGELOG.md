# Changelog

Operator-facing notes for each released version. Heading form is
`## X.Y.Z — YYYY-MM-DD`. `scripts/release_notes.py` copies that
section onto the GitHub Release — write what changed and why, not a
file list. Keep an `## Unreleased` section for work that is not
versioned yet. Bump `VERSION` and move Unreleased into a dated
section in the same change.

## Unreleased

### Fixed

- After a successful GitLab review, Creasy marks the token user
  as reviewed so the Re-request button appears. It does not
  approve the merge request.

## 0.8.0 — 2026-09-10

A full review starts when the token user is assigned as a reviewer.
Comments keep only `@name /ask`. `/review`, `/reset`, and usage
notes are gone.

### Added

- Assigning the `.env` token user (or a `REVIEW_MENTION` alias) as
  a GitLab or Azure reviewer starts a review. Opening an MR or PR
  also starts a review only when that user is already a reviewer.
  Jobs do not assign that user themselves.
- There is no `/review` or `/reset` comment command. A leftover
  `@name /review` or `@name /reset` is ignored. Use assign /
  re-request for a full review and `@name /ask` for a follow-up.
  A mention or command alone is ignored; Creasy no longer posts a
  usage note.

### Fixed

- Adding a teammate as an Azure reviewer no longer starts a Creasy
  review when the bot is already on the PR.
- `@name /ask` at the end of a comment still runs when the question
  is written above the command.
- `@name /ask` still runs when the GitLab token user id is not
  resolved yet, as long as `REVIEW_MENTION` or the token username
  is known.
- A later `/ask` on a resumed session no longer posts the previous
  review as the answer.
- Leftover queued jobs after a restart run oldest-first.

### Changed

- Review notes use Turkish group and field labels (`Özet`, `Kritik`,
  `Kod`, `Sorun`, `Öneri`). English stays only for identifiers and
  technical terms.

## 0.7.0 — 2026-09-09

Operators can change the review model from the dashboard. Comments
run only as `@name /review`, `@name /ask`, or `@name /reset`, and
the answer lands on the same thread as the request.

### Added

- Dashboard Settings page to change the OpenCode model and turn
  timeout without restarting. Values persist in
  `DATA_DIR/settings.json` and apply to new jobs.
- `@mention` of the token user (or `REVIEW_MENTION` aliases) plus
  `/review`, `/ask`, or `/reset` in the same comment runs that
  command on GitLab and Azure. A mention or a command alone posts a
  usage note and does not call OpenCode. The token user is assigned
  as a reviewer on review and ask jobs.
- Comment jobs reply on the same GitLab discussion or Azure thread
  as the request. If that reply fails, the overview note is still
  posted. An `@ask` or `@review` on an inline code-range comment
  includes the user’s text and that file/line span in the prompt.

## 0.6.1 — 2026-09-08

Reviews use a general `code-reviewer` agent. Notes are Turkish
with English technical terms.

### Changed

- The review agent is now `code-reviewer` (GitLab MRs and Azure
  PRs). `OPENCODE_AGENT` defaults to `code-reviewer`. The installer
  still writes `gitlab-reviewer.md` so old `.env` values work.
- The agent no longer treats every change as C++ or fills an
  Improvement section. It loads a language skill from the changed
  paths (`python`, `javascript`, `go`, `rust`, `java`, `csharp`,
  `cpp`, `php`, `ruby`, `kotlin`, `swift`, `scala`, `shell`) and
  flags only hunks with a concrete failing case. C++ dialect
  detection lives in the `cpp` skill, not the agent. Explanations
  are Turkish; API names and terms such as buffer overflow stay
  English.

## 0.6.0 — 2026-09-08

The dashboard can require a login. Executable zips now include the
review agent pack. Job issue reports include redacted diagnostics.

### Added

- Dashboard login page. Set `DASHBOARD_USER` and `DASHBOARD_PASSWORD`
  in `.env`. Opening `/jobs` asks for those credentials; the password
  is not put in the URL or shown in the sidebar. A session cookie
  unlocks job APIs. `DASHBOARD_TOKEN` still works as an API header
  for scripts. Webhooks are unchanged.
- Executable zips include `opencoderman/agents`,
  `opencoderman/skills`, and `install-review-agent` scripts. Those
  copy only the review agent and skills into `~/.opencode`; they do
  not replace an existing OpenCode CLI. The zip does not include
  git history, vendor, or the rest of the submodule.
- Job and system diagnostics for dashboard issue reports. Each job
  stores a redacted stage snapshot (provider, collection URL, HTTP
  status, git/Azure error class, token_set/token_chars). App start
  logs the same flags. Report zips include `job/diagnostics.json`
  and `system/diagnostics.json` plus recent FAIL lines. Tokens,
  Basic/Bearer headers, and URL userinfo are stripped.

## 0.5.2 — 2026-09-08

Azure REST already accepted the PAT. Git clone still failed because
TFS offers Windows auth and Creasy’s askpass was `echo`.

### Fixed

- Azure git clone sends Basic `pat:<PAT>` on the git command (same
  as REST) and uses an askpass that returns that PAT. Job logs now
  include `extraHeader`, `askpass`, and `token_chars`.

## 0.5.1 — 2026-09-08

Azure DevOps Server 2022.2 reviews were 404ing because the collection
path was dropped, then failing auth because the PAT was sent the
cloud way.

### Fixed

- PR fetch includes `/tfs/<Collection>` even when `AZURE_DEVOPS_URL`
  is only the host. The collection is taken from the Service Hook or
  the PR URL and stored on the client. A collection-scoped git path
  is tried after a project-GUID path returns 404.
- PAT auth uses Basic `pat:<PAT>` (IIS rejects an empty username).
  Clone URLs stay under `/tfs/<Collection>/…/_git/…` and are not the
  REST `_apis` URL. Git also sends the same Basic header.

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
