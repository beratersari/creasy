# Creasy — Code Review Easy

A new GitLab-triggered code review service. It receives merge-request webhooks, runs a deep OpenCode analysis of the MR against the full cloned codebase, and posts the result as an MR comment plus optional inline diff threads. Concurrency and OpenCode process handling follow [opencode_manager](https://github.com/beratersari/opencode_manager). Webhook classification follows [gitlab_code_reviewer](https://github.com/beratersari/gitlab_code_reviewer). The clone is **not** deleted when a review job ends; it is deleted only when the MR is closed or merged.

Workspace is empty. This is a greenfield Python service.

---

## What we are building

```
GitLab webhook
    │
    ▼
POST /webhook  (ack immediately)
    │
    ├─ MR open / update (new commits) / reopen
    │       └─ enqueue one review job for that MR
    ├─ Note on an MR whose body contains "/review"
    │       └─ enqueue a full review job (resume ses_* if we have one)
    ├─ Note on an MR whose body contains "/ask"
    │       └─ enqueue a follow-up on the same ses_* (question only, no full review prompt)
    └─ MR close / merge
            └─ stop any live job for that MR, then delete its workspace
    │
    ▼
Job manager (max_concurrent_jobs, per-MR FIFO comment queue, one running job per MR)
    │
    ▼
Workspace  work_dir/{project_id}-{mr_iid}
    clone once, then git fetch + checkout source branch on later reviews
    │
    ▼
Per-job `opencode serve` on 127.0.0.1:<ephemeral>
    prompt = MR metadata + merge-base + diff stat + “analyze from the separation point”
    │
    ▼
Post last assistant markdown as a GitLab MR note
Post one Discussions-API thread per structured finding
Kill this serve. Keep the clone until close/merge.
```

---

## Locked decisions

### 1. One process, OSM approach in-process — not an OSM sidecar

Do **not** HTTP-call a running OpenCode Session Manager. OSM always hard-deletes the clone at job end, keys work by `jira_id`, and does not check out the source branch. Creasy needs the opposite lifecycle.

Port the proven OSM internals into this repo:

- Job queue + `max_concurrent_jobs`
- One **running** job per MR; later comments for that MR sit in a FIFO queue (not OSM’s 409 / not coalesce-to-latest)
- Per-job `opencode serve` on an ephemeral localhost port
- Session create / resume against a stable workspace path
- Force-kill of that serve tree only
- Boot leftover-pid reap (do not resume crashed jobs)
- Isolated git env, origin userinfo scrub, Windows-safe folder names

Do **not** copy OSM’s n8n JSON, `POST /jobs` public contract, or `DELETE /sessions` Jira flow. **Do** adapt OSM’s jobs-tab dashboard (list, detail, chat, logs) and add cancel — OSM’s UI is GET-only; ours is not.

Do **not** copy gitlab_code_reviewer’s `opencode --auto` CLI wrapper or per-file sequential review. Use it only for webhook event rules and GitLab API shapes.

### 2. Clone lives with the MR, not with the job

| Event | Clone |
|---|---|
| First review for an MR | Clone HTTPS repo into `work_dir/{project_id}-{mr_iid}` and check out `source_branch` |
| Later `/review` or new commits | Reuse the same path: `git fetch` + checkout + reset to the latest source SHA. Start a **new** serve. Resume `session_id` if we still have one |
| Review job finishes (success or fail) | Kill serve. **Keep the clone** |
| MR `close` or `merge` | If a job is running, stop it. Then hard-delete that MR’s clone and workspace record |
| Process restart | Reap leftover serve pids. Do **not** delete surviving clones. Do **not** resume in-flight reviews (mark them failed; next webhook can start a new job) |

This is the one intentional break from OSM’s “always delete, then re-clone to the same path” rule. Same-path resume still works because we never delete until the MR is gone.

“Delete the related MR” means the **local workspace for that MR**, not the GitLab merge request itself.

### 2b. Session resume after a later `/review` or `/ask`

Yes. A completed review does not throw away the OpenCode conversation.

OpenCode sessions live in the global `opencode.db`, keyed by workspace `directory`. The serve process is killed when the job ends; the `ses_*` id is not.

**Decision (user):** later comments continue the same session via `/review` or `/ask`. Ordinary notes are ignored.

Shared resume flow for both commands after a finished job:

1. Load the workspace record for `{project_id}-{mr_iid}`.
2. Reuse the existing clone (fetch/checkout if the branch moved).
3. Start a **new** `opencode serve` on that same absolute path.
4. Resume the stored `session_id` (`GET /session/{id}` then continue).
5. POST a new user message (shape depends on the command — see below).
6. Post the new assistant reply as an MR note, then post any
   structured findings as GitLab diff threads.
7. Persist the same (or replacement) `session_id` on the workspace.

| Command | Prompt on resume | If no prior session |
|---|---|---|
| `/review [notes]` | Full review prompt again: MR metadata, merge-base, `--stat`, file list, project rules, “analyze from the separation point”, plus the remainder after `/review` | Create a session and run the full review |
| `/ask <question>` | Only the question (plus a one-line “SHA changed to …” if the branch moved). Do **not** rebuild the full review prompt. The previous review is already in chat history. | Still run: clone if needed, create a session, send a short context (title, source→target, changed-file list) + the question. Do not require a prior `/review`. |

`/ask` with no question text after the command: ignore the webhook (200) and do not start a job. Optionally we can post “usage: `/ask <question>`” later; v1 just ignores.

Rules:

- First job for an MR: no `ses_*` yet → create a session, save it.
- Later `/review` or `/ask` on the same MR: resume that `ses_*` so the agent still has the previous review in chat history.
- If OpenCode rejects the id (expired / unknown), create a **new** session and continue. Do not fail the job. Save the new id. For `/ask` after a rejected id, include the short MR context so the new session is not blind.
- Mid-job hang retry (we already posted the user message): resume the **same** id only. Do not invent a blank session and pretend it is a continue.
- Different MRs never share a session.
- Process restart does not keep the serve, but the next `/review` or `/ask` still resumes from `opencode.db` because the clone path is unchanged.
- Comments with neither `/review` nor `/ask` are ignored. Thank-yous and LGTM do not spend an OpenCode slot.

### 3. Triggers

Taken from gitlab_code_reviewer, plus the close/merge cleanup the old service never had.

| GitLab event | Action |
|---|---|
| `object_kind=merge_request`, `action` in `open`, `reopen` | Enqueue review |
| `object_kind=merge_request`, `action=update` **and** `oldrev` is present (new commits) | Enqueue review |
| `object_kind=merge_request`, `action` in `close`, `merge` | Cleanup workspace; do not review |
| `object_kind=note`, `noteable_type=MergeRequest`, body contains `/review` | Enqueue full review (resume `ses_*` if stored) |
| `object_kind=note`, `noteable_type=MergeRequest`, body contains `/ask` + question text | Enqueue follow-up on the same `ses_*` |
| Everything else | 200 ignored |

If both `/review` and `/ask` appear in one note, the **first** command token wins.

Extra guards:

- Ignore notes authored by the token’s own user so our posted review cannot retrigger.
- Treat `/review` and `/ask` as command tokens (word-style match), not substrings of “preview” / “task”.
- Skip draft MRs by default (`SKIP_DRAFT_MRS=true`) for auto MR events. `/review` and `/ask` on a draft still run (explicit human request).
- Title/description-only MR updates do not review (`oldrev` missing).

Webhook HTTP response is always an immediate ack (`accepted`, `queued`, or `ignored`). A comment that arrives while that MR already has a running job is **accepted and queued**, not 409. Review work never holds the GitLab webhook socket.

### 4. Each comment is a separate job (same as OSM)

Same contract as OSM: **one inbound trigger → one job → one prompt → one terminal result**. We do not keep a job or an `opencode serve` open waiting for the next GitLab comment.

| Layer | Lifetime | Identity |
|---|---|---|
| Job | One webhook / one comment | New `job_id` every time (`job_…`) |
| OpenCode serve | Bound to that job | Started at job start, force-killed at job end |
| OpenCode session | Bound to the MR | Stored `ses_*` on the workspace, reused by later jobs |
| Clone | Bound to the MR | Kept until close/merge |

So: `/review` then later `/ask` is **two jobs**, one session.

```
comment 1 "/review"  →  job_aaa  →  create ses_1  →  post review note  →  kill serve
comment 2 "/ask …"   →  job_bbb  →  resume ses_1  →  post answer note  →  kill serve
comment 3 "/review"  →  job_ccc  →  resume ses_1  →  post review note  →  kill serve
```

Why not one long-lived job:

- Comments can arrive minutes or hours later. Holding serve/RAM for that is OSM’s rejected model.
- Each job has its own timeout, retry_count, log file, and status. History stays inspectable (`GET /reviews/{project}/{mr}` lists jobs).
- OSM already proved resume: new serve + same path + old `ses_*`.

**This is the break from OSM.** OSM returns **409** and drops a second POST for the same `jira_id` while a job is live. Creasy does **not** drop comments.

Per-MR FIFO:

- At most **one running serve** per MR.
- A later `/review` or `/ask` while that MR is already running or already has queued jobs → mint a **new** `job_id`, append it to that MR’s queue, ack `queued`.
- When the running job finishes (success or fail), pop the next queued job for **that same MR** and start it (resume `ses_*`, same clone).
- Jobs for one MR run **in arrival order**. `/review` then `/ask` then `/review` is three jobs, all executed, none discarded.
- Different MRs still share the global `MAX_CONCURRENT_JOBS` cap. If the host is full, the next job waits in the global dispatcher until a slot frees; order within an MR is still FIFO.

```
MR !17  job_aaa  /review     running
        job_bbb  /ask Q1     queued
        job_ccc  /ask Q2     queued

job_aaa ends → start job_bbb (resume ses_*) → then job_ccc
```

Auto MR events (`open` / `update` with new commits / `reopen`) are not comments:

- If that MR already has a **running or queued** job, **do not** enqueue another auto-review. The queued/running work will see the latest SHA when it fetches. This is the only coalesce, and it is only for webhook noise (push storms, title-less `update` we already ignore).
- Explicit `/review` and `/ask` are **never** dropped.

Close/merge: cancel the running job **and drain/cancel** that MR’s queued jobs, then delete the workspace. Do not start queued comments after the MR is gone.

### 5. How we understand the MR changes

We do **not** trust GitLab’s `/changes` payload as the source of truth. That API truncates large diffs (`too_large`), paginates badly, and is what the old per-file reviewer used. Creasy treats the **clone + git** as the change set, and GitLab only as metadata.

**Metadata (GitLab API)**

`GET /projects/:id/merge_requests/:iid` (and commits if useful):

- title, description, author, web_url
- `source_branch`, `target_branch`
- `sha` (source HEAD), `diff_refs.base_sha` / `start_sha` / `head_sha`
- draft / state

**Workspace (git, after clone or fetch)**

1. Fetch both `source_branch` and `target_branch`.
2. Check out `source_branch` at the MR `sha` (detached or reset). Working tree = what the MR would merge.
3. Compute the **three-dot** diff, which is “commits on the source since it diverged from target” — the same thing GitLab shows as the MR:

```
git diff --stat <target>...HEAD
git diff <target>...HEAD
```

`<target>` is `origin/<target_branch>` (or `diff_refs.base_sha` if we want the exact GitLab comparison). Prefer `base_sha` when GitLab sends it so our diff matches the MR UI even after target moved.

4. Build a change index from that diff: path, status (added/modified/deleted/renamed), additions/deletions. Filter with `REVIEW_EXTENSIONS`, `MAX_FILE_SIZE_KB`, skip binaries.

**What OpenCode actually sees (no full diff in the prompt)**

Pasting the whole unified diff into the first message is a bad default. Large MRs blow the context, the model skims hunks, and it fights the point of a codebase-aware agent. The clone is already at source HEAD; OpenCode can run `git` and open files.

The prompt is a **map**, not the change set:

1. MR title, description, author, `source → target`, HEAD sha.
2. **Separation point:** merge-base SHA (`git merge-base origin/<target> HEAD`, or GitLab `diff_refs.base_sha` when present).
3. `git diff --stat <base>...HEAD` and the changed-path list (added/modified/deleted/renamed). Filter with `REVIEW_EXTENSIONS` / `MAX_FILE_SIZE_KB`; skip binaries.
4. Project rules if present (`agent/rules/CODE_REVIEW.md`, else `.creasy/CODE_REVIEW.md`).
5. Optional remainder after `/review`.
6. Hard instructions:
   - Analyze **from the separation point**, not the whole repo history.
   - Run `git log <base>..HEAD` and `git diff <base>...HEAD` (and per-path diffs) yourself. Do not assume the prompt contains hunks.
   - For each changed path, read the current file and its callers/tests. Review the change in context.
   - Do not commit, push, or edit files.

Creasy still **computes** the three-dot stat (and may log the full diff under the job log for humans). That output does **not** go into the OpenCode user message.

| Layer | Role |
|---|---|
| Merge-base + `--stat` + path list in the prompt | Scope: what diverged since the MR split from target |
| OpenCode tools on the clone | The actual analysis: `git diff <base>...HEAD`, file reads, follow-ups |
| GitLab `/changes` | Not used. Fallback only if `git` cannot produce a stat |

We do **not** walk each file through a separate OpenCode call (old reviewer). We do **not** dump the unified diff into the prompt.

**`/ask <question>`**

1. Same workspace (fetch if SHA moved). Resume `ses_*`.
2. Prompt is the question only. If the SHA changed since the last job, prepend a one-line note + updated `--stat` so the agent is not answering about stale files.
3. If this is a new/rejected session, prepend short MR context (title, branches, changed-file list) — still not a full review prompt.

`/review-in-detail` stays out of v1. The product is **one** Overview
MR note per job, plus one GitLab diff thread per structured finding
the agent emits. The note is still the full review. Threads are
anchors on `path` + line range (`x.cpp` 30–40), posted via
`POST /projects/:id/merge_requests/:iid/discussions`. A failed
thread is logged and skipped; it does not fail the job.

The agent reply is the markdown review, plus an optional
`creasy-findings` fence. Creasy strips that fence before posting
the note. Threads come from the fence when present, else from
`#### N. \`path:lines\`` titles. Map each finding onto the
three-dot diff and send GitLab `position` (`base_sha` /
`start_sha` / `head_sha` from the MR `diff_refs`). Line mapping
uses the local `git diff <merge-base>...HEAD`, not a cached
GitLab `/changes` payload. Positions use GitLab’s version SHAs
so the Discussions API will accept them. A failed thread is
logged and skipped; it does not fail the job.

### 6. Git auth is non-interactive

This is a webhook server, not a desktop OSM worker. No GCM popup.

- `GITLAB_TOKEN` is required for private clones and for posting notes.
- Clone URL is `https://oauth2:{token}@{host}/...`.
- Immediately scrub `origin` userinfo after clone (same as OSM).
- `GIT_TERMINAL_PROMPT=0`. Isolated git env. No SSH / `git@`.

### 7. Stack

Python 3.11+, FastAPI, uvicorn, httpx or requests, pydantic, pytest. Windows + Linux. Config via `.env`. No Docker in v1 unless we add it later.

### 8. Dashboard with cancel (v1)

**Decision (user):** ship a jobs dashboard in v1, including cancel. OSM’s `/jobs` page is the visual/API reference; it is GET-only and we will add writes.

Served at `GET /jobs` on the same process (same idea as OSM serving the SPA at `:4096/jobs`).

What you can see (read-only, OSM-shaped):

- Job list with filters: all / active / queued / error / completed. Filter by `mr_key` / project / iid.
- Job detail: status, MR link, trigger (`review` / `ask` / `open` / `update`), model, session_id, timestamps, error.
- Chat snapshot (user prompt + last assistant text).
- Job log and serve log tails.
- Per-MR queue: running job + FIFO waiting comments.

What you can do (not in OSM):

- **Cancel one job** (`POST /api/jobs/{job_id}/cancel`).
  - Running: set stopping, force-kill that serve, finish status `cancelled`, post a short MR note (“review cancelled”), then dispatch the **next** queued job for that MR.
  - Queued: mark `cancelled`, remove from the FIFO, never start it. Other queued comments stay in order.
- **Cancel all for an MR** (`POST /api/mrs/{project_id}/{mr_iid}/cancel`): cancel running + every queued job. Keep the clone (MR is still open). Same as close/merge job-cancel, without deleting the workspace.

What the dashboard must not do:

- Start a review (webhook is still the only producer).
- Delete a clone.
- Edit settings.
- Call OSM `POST /jobs`.

Auth: mutating dashboard routes require `DASHBOARD_TOKEN` (`Authorization: Bearer …` or header `X-Creasy-Token`). GET list/detail can use the same token when it is set; if unset, dashboard binds as open (dev only). Do not reuse `GITLAB_TOKEN` in the browser.

UI: adapt OSM’s jobs-tab look (list + detail + logs). Rename `jira_id` → MR (`project!iid` / `mr_key`). Keep it GET-mostly plus Cancel buttons. Prefer a small SPA under `web/` (copy OSM structure and restyle labels) rather than inventing a new product.

---

## Project layout

```
creasy/
  pyproject.toml
  README.md
  .env.example
  src/creasy/
    __init__.py
    app.py                 # FastAPI + lifespan (boot / shutdown)
    config.py
    api/
      webhook.py           # POST /webhook
      health.py            # GET /health
      dashboard.py         # GET /api/jobs, cancel
    dashboard/             # SPA adapter (OSM jobs-tab look)
    gitlab/
      client.py            # MR, notes, discussions, current user
      events.py            # classify payload → ReviewTrigger | CleanupTrigger | Ignore
    workspace/
      identity.py          # project_id-mr_iid → safe folder
      store.py             # persist MR workspace (path, session_id, last_sha)
      gitops.py            # clone / fetch / checkout / three-dot diff / hard-delete
    jobs/
      models.py
      store.py
      queue.py
      manager.py           # accept, per-MR FIFO, dispatch
      worker.py            # review pipeline
    opencode/
      serve.py             # start/stop per-job serve (OSM pattern)
      session.py           # create/resume, POST prompt, poll idle
    review/
      prompt.py
      format.py            # wrap assistant text as MR markdown
      findings.py          # parse/strip creasy-findings JSON
      position.py          # GitLab discussion position from the diff
    logging.py
  tests/
    test_events.py
    test_identity.py
    test_manager_dedup.py
    test_workspace_lifecycle.py
    test_webhook.py
    test_cancel.py
    fixtures/gitlab/
  web/                     # dashboard frontend
```

Reference OSM modules while implementing `opencode/` and `jobs/`, then write Creasy-owned copies. Do not vendor the OSM repo as a git submodule and do not mutate that GitHub repository.

---

## Runtime contracts

### Config (`.env`)

| Variable | Default | Role |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Public webhook bind |
| `GITLAB_URL` | `https://gitlab.com` | API base |
| `GITLAB_TOKEN` | required | API + clone |
| `WEBHOOK_SECRET` | required in prod | `X-Gitlab-Token` |
| `OPENCODE_MODEL` | `opencode/big-pickle` | `provider/id` |
| `OPENCODE_TIMEOUT` | `1800` | One attempt, seconds |
| `OPENCODE_RETRY_COUNT` | `2` | Attempts, first included |
| `OPENCODE_AGENT` | `gitlab-reviewer` | OpenCode agent id. Installer writes the read-only `gitlab-reviewer` agent |
| `MAX_CONCURRENT_JOBS` | `2` | Live serves |
| `DATA_DIR` | `./data` | clones, logs, job/workspace JSON |
| `SKIP_DRAFT_MRS` | `true` | |
| `REVIEW_EXTENSIONS` | common source suffixes | |
| `MAX_FILE_SIZE_KB` | `500` | Drop oversized paths from the stat/file list |
| `DASHBOARD_TOKEN` | empty (dev) | Required in prod for dashboard GET+cancel |

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | ready, running/queued counts, workspace count |
| `POST` | `/webhook` | GitLab hook |
| `GET` | `/jobs` | Dashboard SPA |
| `GET` | `/api/jobs` | List/filter jobs (`filter`, `mr_key`, page) |
| `GET` | `/api/jobs/{job_id}` | Job detail + system logs |
| `GET` | `/api/jobs/{job_id}/chat` | Prompt + assistant snapshot |
| `GET` | `/api/jobs/{job_id}/logs` | Job log tail |
| `GET` | `/api/queue` | Per-MR FIFO (running + waiting) |
| `POST` | `/api/jobs/{job_id}/cancel` | Cancel one running or queued job |
| `POST` | `/api/mrs/{project_id}/{mr_iid}/cancel` | Cancel running + queued for that MR |
| `GET` | `/reviews/{project_id}/{mr_iid}` | Latest job + workspace (debug) |

No public `POST /jobs`. The webhook handler is the only producer. `GET /` serves the dashboard (or redirects to `/jobs`).

### Workspace record

```
mr_key, project_id, mr_iid, clone_path, source_branch, last_sha,
session_id, last_job_id, updated_at
```

### Job record

OSM-like: `job_id`, `mr_key`, status, live, serve pid/port, clone_path, session_id, prompt, text, error, timestamps. Persist under `DATA_DIR/jobs/`.

---

## Review pipeline (worker)

1. Load MR + changes from GitLab.
2. Ensure workspace: clone if missing, else fetch source + target and checkout the MR `sha`. Persist `last_sha`.
3. Resolve merge-base. Compute `git diff --stat <base>...HEAD` and the path list. Build the prompt from that map — **do not** paste the unified diff.
4. Allocate free port. `opencode serve --hostname 127.0.0.1 --port <n>` with cwd = clone. Health-wait `GET /global/health`.
5. Create or resume `ses_*`. Send `x-opencode-directory: <clone>`.
6. POST the review prompt once. Drive until idle / timeout / hang / serve-dead. Retry per `OPENCODE_RETRY_COUNT` with OSM hang/resume rules (do not invent a blank session mid-job).
7. Split findings JSON out of the last assistant text, or scrape
   `####` titles. Format and post the markdown as an MR note.
   Then post each finding as a GitLab diff discussion (best
   effort). On hard failure, post a short error note and skip
   threads.
8. Abort session (best effort). Force-kill this serve tree. **Do not delete the clone.**
9. Dispatch the next queued job for this `mr_key`, if any.

On `close`/`merge`:

1. If a running job exists for `mr_key`, mark stopping, kill serve, finish it as cancelled. Cancel every queued job for that MR (do not run them).
2. Hard-delete `clone_path` with retries (Windows file locks).
3. Drop the workspace record. Keep job history.

---

## Tests (v1, no live GitLab / OpenCode required)

- `events.py`: open / update-with-oldrev / update-without-oldrev / close / merge / `/review` note / `/ask` note / `/ask` with empty question / bot note / draft / unrelated note / both commands (first wins).
- `identity.py`: safe folder, path stays under `work_dir`.
- `manager`: second `/ask` while `/review` is running is queued (not 409, not dropped); both jobs run in order; two different MRs both run; close cancels running + queued jobs.
- `workspace`: missing → clone; existing → fetch path; close → directory gone.
- Diff: prompt contains merge-base + `--stat` + paths, never the full unified diff; GitLab `/changes` is not required.
- Webhook handler: secret 401, immediate 200, background enqueue.
- Worker with a fake OpenCode client: success posts a note; failure posts an error note; clone still exists after finish.
- Findings: `creasy-findings` JSON is stripped from the note when present; otherwise `####` titles supply path/lines. Each valid finding becomes a discussion; a 400 from GitLab does not fail the job. Rebase: merge-base is the **new** target tip for mapping, but discussion SHAs still come from MR `diff_refs`.
- Large-file discussions: one thread per planted line in a 1000+ line file (no OpenCode).
- Live OpenCode review (`tests/test_opencode_review.py`) is skipped unless `CREASY_LIVE_OPENCODE=1`.
- Cancel: running job is killed and next queued job for that MR starts; cancelling a queued job does not touch the runner; cancel-all-for-MR leaves the clone on disk.

A small `tests/mock_gitlab_webhook.py` (menu or `--event`) can replay fixtures against a running server, same idea as the old mock server.

---

## Implementation order

1. **Scaffold** — package, config, logging, app lifespan, health.
2. **GitLab events + client** — parse payloads, fetch MR/changes, post notes, resolve bot user id. Tests first for `events.py`.
3. **Workspace + gitops** — identity, store, clone/fetch/checkout/delete. Tests for lifecycle.
4. **Job manager** — global slot cap, per-MR FIFO comment queue, one running job per MR. Tests.
5. **OpenCode serve/session** — port OSM control loop, adapted so finish does not delete the clone.
6. **Review prompt + worker + webhook wiring** — end-to-end path with fakes.
7. **Close/merge cleanup** — stop live job, delete workspace.
8. **Dashboard + cancel** — OSM-style jobs UI, list/detail/chat/logs, cancel one job and cancel-all-for-MR.
9. **README + `.env.example` + mock webhook script**.

Each step should leave tests passing before the next starts.

---

## Out of v1

- `/review-in-detail`, per-file parallel reviews (old reviewer TODOs)
- Calling a remote OSM instance
- Docker / systemd unit (document later)
- Auto-approve OpenCode permissions
- SSH clones

---

## Risks

- **Disk**: clones stay until MR close. Large monorepos × many open MRs. Mitigate with `DATA_DIR` on a large volume; optional later TTL is out of v1.
- **RAM**: each live serve is hundreds of MB. `MAX_CONCURRENT_JOBS` is the budget.
- **Windows locks**: delete only after serve is force-killed; retry delete like OSM.
- **Webhook loops**: ignore our own notes; never put a bare `/review` in the posted review template.
- **GitLab `update` noise**: require `oldrev` so assignee/label edits do not spend an OpenCode slot.
