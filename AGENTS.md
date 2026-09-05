# AGENTS.md — Creasy

This file is binding for anyone implementing or changing this repo.
The longer design lives in [plan.md](plan.md). If this file and the
plan disagree, **fix the plan** — do not invent a third design.

Creasy is a GitLab-triggered code review service. A webhook starts a
job. The job clones the MR branch, runs one `opencode serve`, and posts
the last assistant message as an MR note plus one GitLab diff thread
per structured finding.

It is not OpenCode Session Manager. Do not add `POST /jobs`, n8n
callbacks, or Jira ids. Do not call a remote OSM instance.

## Intentional product choices

These look like bugs. They are not.

1. **The product of a job is one MR note plus optional diff threads.**
   Last assistant markdown (findings JSON stripped) as the Overview
   note, then one Discussions-API thread per structured finding
   (`path` + line range). A later `/review` that matches an existing
   unresolved Creasy thread replies there, unless the last Creasy
   note is ≥ 90% similar (Ratcliff-Obershelp / token Jaccard /
   3-gram Jaccard) — then skip the reply and do not open a new
   thread. Short error / cancelled notes have no threads. A failed
   thread does not fail the job. No git push.
2. **The clone lives with the MR, not the job.** Delete it only on MR
   `close` / `merge`. A finished review keeps the tree so the next
   `/review` or `/ask` can resume `ses_*` on the same path.
3. **Each comment is a new job.** New `job_id`, new serve, one prompt,
   one note, then kill that serve. Do not hold a serve open waiting
   for the next GitLab comment.
4. **Comments queue FIFO per MR.** A later `/review` or `/ask` while
   that MR is running is **queued**, not 409, not coalesced to “latest
   only”. Auto `open` / `update` / `reopen` are skipped if that MR
   already has a running or queued job.
5. **Do not put the unified diff in the prompt.** Give merge-base,
   `git diff --stat <base>...HEAD`, and the path list. OpenCode reads
   the tree and runs git itself. Do not filter paths by extension.
6. **Separation point is a live `git merge-base`**, not a cached
   GitLab `base_sha`. After a rebase onto a moved target, the old
   fork commit is still in the repo and would pull target-branch
   commits into the review.

## Hard rules

### Webhook

- `POST /webhook` acks immediately. Never hold that socket for clone
  or OpenCode.
- Verify `X-Gitlab-Token` against `WEBHOOK_SECRET` when the secret is
  set. Missing/wrong → **401**.
- Classify in `creasy.gitlab.events`. Do not re-parse payloads in the
  worker.
- MR `open` / `reopen` → enqueue review. `update` only if `oldrev` is
  present. `close` / `merge` → cancel jobs and delete the clone.
- Note on a merge request: first command token wins. `/review` → full
  review job. `/ask <question>` → follow-up. Empty `/ask` → ignore.
  Notes from the token’s own user → ignore.
- Draft MRs: skip auto events when `SKIP_DRAFT_MRS` is true. Explicit
  `/review` and `/ask` still run.
- The webhook is the **only** job producer. The dashboard must not
  start a review.

### Jobs and concurrency

- Identity is `{project_id}-{mr_iid}` (Windows-safe). That is the
  clone folder and the queue key.
- At most one **running** serve per MR. Global cap is
  `MAX_CONCURRENT_JOBS`.
- Persist jobs and the per-MR FIFO. Survive a process that stays up.
- Boot: reap leftover serve pids. Mark leftover **running** jobs
  `error` (“not resumed”). Re-enqueue leftover **queued** jobs, then
  dispatch. Do not resume an interrupted OpenCode turn.
- Shutdown: stop accepting work, cancel queued jobs, signal running
  jobs, join workers. Do not delete clones on shutdown.

### Clone and git

- HTTPS only. Inject `GITLAB_TOKEN` as `oauth2:{token}@host`, then
  **scrub origin userinfo**. `GIT_TERMINAL_PROMPT=0`. No SSH.
- First review: clone into `DATA_DIR/workspaces/{mr_key}`. Later
  jobs: `git fetch` source + target, checkout the MR `sha`.
- After checkout, `merge-base origin/<target> HEAD`. Use GitLab
  `base_sha` only if merge-base fails.
- Job end: kill **this** serve. **Keep the clone.**
- Close/merge: cancel running + queued for that MR, then hard-delete
  the clone (retries; Windows locks). Keep job history.

### OpenCode

- One `opencode serve` per job on `127.0.0.1:<ephemeral>`. Never
  hardcode a serve port. Never `opencode --auto`. No permission
  auto-approve.
- Scope HTTP with `x-opencode-directory: <clone>`.
- First job for an MR: create `ses_*` and store it on the workspace.
  Later jobs: resume that id. If OpenCode rejects it, create a new
  session and continue (do not fail the job). Mid-job hang retry:
  same `ses_*` only — do not invent a blank session.
- `/review` (and auto MR events): full map prompt (title, description,
  draft, labels, latest pipeline, branches, merge-base, stat, paths,
  `/review` remainder). Do not paste project rules into the prompt.
  The reviewer reads `agent/rules/CODE_REVIEW.md` from the clone if
  that file exists.
  One OpenCode turn. Parse findings from an optional `opencoderman-findings`
  fence or from `#### N. \`path:lines\`` titles. If GET `/message`
  is unreadable (400), create a new `ses_*` and continue.
- `/ask`: question only. If SHA moved, prepend a one-line note +
  updated `--stat`. If the session is new/rejected, prepend short MR
  context (including draft, labels, pipeline, and a clipped description).
- OpenCode only. No Codex.
- Jobs use the `gitlab-reviewer` agent (`OPENCODE_AGENT`, default
  `gitlab-reviewer`).
  Agent and skill files live in the OpenCoderman pack
  (`opencoderman` submodule: `agents/*.md`, `skills/*/SKILL.md`).
  That pack also vendors the
  OpenCode CLI (`vendor/bin/<os>/`) in its CI artifact; `install.py`
  copies it into `~/.opencode/bin`. `install-opencode` uses the same
  replace rules as that pack: rename `~/.opencode` to
  `~/.opencode_backup_YYYYMMDD_HHMMSS`, drop other OpenCode dirs from
  PATH, write a clean `~/.opencode` (agents, skills, CLI). Do not copy
  into `~/.config/opencode`; leftover trees there are renamed to
  `~/.config/opencode_backup_*` so OpenCode does not load a second
  copy. Then install the vendored CLI (Creasy `vendor/bin`, else the
  pack's, else the backup) and prepend `~/.opencode/bin`. Add a new
  agent in that submodule, not under `scripts/`.

### Dashboard

- `GET /jobs` plus `/api/jobs`, chat, logs, queue.
- Writes: `POST /api/jobs/{id}/cancel` and
  `POST /api/mrs/{project}/{iid}/cancel`.
- Cancel running: stop that serve, mark `cancelled`, post a short MR
  note, start the next queued job for that MR.
- Cancel queued: mark `cancelled`, leave the runner alone.
- Cancel-all-for-MR: cancel running + queued, **keep** the clone.
- When `DASHBOARD_TOKEN` is set, require it on dashboard routes.
  Do not send `GITLAB_TOKEN` to the browser.

### Code layout

Keep packages honest:

| Package | Owns |
|---|---|
| `creasy.gitlab` | Webhook classify, GitLab HTTP |
| `creasy.workspace` | `mr_key`, clone path, fetch/checkout, merge-base, delete |
| `creasy.jobs` | Store, FIFO, dispatch, worker |
| `creasy.opencode` | Serve + session drive |
| `creasy.review` | Prompt text, MR note markdown, findings JSON |
| `creasy.api` | HTTP only — no review logic |

Do not put OpenCode calls in the webhook handler. Do not put GitLab
note or discussion posting in `opencode/`.

### Tests

- `pytest` must stay runnable with no live GitLab and no `opencode`
  binary. Fake the runner for manager/webhook tests.
- Event tests cover open / update-with-and-without-`oldrev` / close /
  merge / `/review` / `/ask` / empty `/ask` / bot note / first-command.
- Manager tests cover FIFO queue, parallel MRs, skipped auto events,
  cancel running/queued, close drains the queue.
- Rebase: merge-base is the **new** target tip; target-only files are
  not in the path list.
- Findings JSON is stripped from the MR note. Each valid finding is
  posted as a discussion. A failed thread does not fail the job.
- Do not add tests that require network unless they are clearly marked
  and skipped by default.
- Live OpenCode review coverage is `tests/test_opencode_review.py`.
  Skip unless `CREASY_LIVE_OPENCODE=1` and an `opencode` binary is
  present. Fake GitLab. Default `pytest` must not start a serve.

## Commit conventions

```
<type>(<scope>): <summary>
```

- **type:** `feat` `fix` `refactor` `test` `docs` `chore`
- **scope** (optional): `webhook` `gitlab` `jobs` `git` `opencode`
  `review` `dashboard`
- **summary:** imperative, ≤ 72 characters, no trailing period
- Body: why, not a file list. Wrap at 72.
- Footer: `Fixes #n` when it closes an issue

Examples:

```
feat(webhook): queue /ask while a review is running
fix(git): compute merge-base after rebase instead of GitLab base_sha
test(jobs): cover cancel-all leaving the clone on disk
docs: add AGENTS.md with rules and commit conventions
```

Do not:

- Commit `.env`, tokens, or `DATA_DIR` clones
- Mix unrelated refactors with a behavior fix
- Use `update` / `wip` / `misc` as the subject
- Force-push `main`

One logical change per commit. Run `pytest` before you push.
