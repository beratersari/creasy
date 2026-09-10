# Creasy

Code review easy. GitLab or Azure DevOps webhooks trigger a deep OpenCode review of a merge request or pull request against the cloned codebase.

Agent rules and commit conventions: [AGENTS.md](AGENTS.md).
OpenCode agents and skills live in
[OpenCoderman](https://github.com/beratersari/opencoderman)
(`opencoderman` submodule) so other projects can reuse them.

Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/beratersari/creasy.git
# existing checkout:
git submodule update --init --recursive
```

Clones live with the MR or PR. They are deleted only when it is closed, merged, or abandoned. A full review starts when the token user is assigned or re-requested as reviewer, or when someone writes `@mention /review`. Each `@mention /ask` is a follow-up reply on that thread only. A mention without `/ask` or `/review` gets a usage note on that thread. A slash command alone is ignored. There is no `/reset` command.

## Run

Offline (OSM-style zip: bundled CPython + wheels + OpenCode CLI):

```bash
# On a machine with network
python packaging/build_dist.py --in-place    # or scripts/vendor.bat

# On the air-gapped host (or after unpacking the CI artifact)
install.bat                 # .venv from vendor/python/windows/python.exe
install-opencode.bat        # backup ~/.opencode, unhook other PATH entries, install CLI + agents/skills
# edit .env (GITLAB_TOKEN, WEBHOOK_SECRET, OPENCODE_MODEL)
start.bat                   # new window + wait for /health
```

Online / from a checkout:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
# set GITLAB_TOKEN, WEBHOOK_SECRET, OPENCODE_MODEL
npm --prefix web install
npm --prefix web run build
python -m creasy
```

Dashboard: http://127.0.0.1:9001/jobs  
Settings (model + timeout): http://127.0.0.1:9001/settings  
Set `DASHBOARD_USER` and `DASHBOARD_PASSWORD` in `.env` so the
dashboard shows a login page. Webhooks do not use those values.
Model and timeout can be changed on Settings without editing `.env`;
they persist in `DATA_DIR/settings.json` and apply to new jobs.  
GitLab webhook: `POST /creasy/webhook/gitlab`  
Azure webhook: `POST /creasy/webhook/azure`  
Health: `GET /health`

The product version is the `VERSION` file (`/health` and `/api/meta` expose it).
Bump with `python scripts/bump_version.py minor`. What changed is in
[`CHANGELOG.md`](CHANGELOG.md).

Download **GitHub Release** executable zips
(`creasy-<version>-windows-x64.zip`, `linux-x64`, `darwin-arm64`),
not the tag’s “Source code” zip. Each zip is one `creasy` /
`creasy.exe`, `.env.example`, `opencoderman/agents`,
`opencoderman/skills`, and `install-review-agent` scripts. Copy the
example to `.env` next to the binary and run it. Reviews still need
`opencode` on `PATH`. Run `install-review-agent.bat` (or `.sh`) once
to copy those agent and skill files into `~/.opencode` without
replacing an existing OpenCode install.

## Webhooks

The dashboard cannot start a review. GitLab and Azure each post to
their own URL. Creasy must be reachable from the GitLab or Azure
host, not only from your laptop.

### GitLab

1. In the project or group: **Settings → Webhooks**.
2. **URL:** `http://<creasy-host>:9001/creasy/webhook/gitlab`  
   Use `https://` if Creasy is behind TLS. Do not use the Azure path.
3. **Secret token:** the same value as `WEBHOOK_SECRET` in `.env`.  
   GitLab sends it as `X-Gitlab-Token`. A missing or wrong secret is **401**.
4. Enable these triggers only:
   - **Merge request events** — `open` starts a review; `close` / `merge` cancel jobs and delete the clone; `update` and `reopen` are ignored.
   - **Comments** — `@<bot> /ask <question>`.
5. If Creasy is plain HTTP or uses an intercept certificate, leave **Enable SSL verification** unchecked.
6. Save, then **Test** with a Merge request hook. Creasy should answer immediately (`accepted`, `queued`, or `ignored`).

`GITLAB_TOKEN` needs the `api` scope so Creasy can clone over HTTPS, post the overview note, and open diff threads. Use a dedicated bot user: notes from that user are ignored.

### Azure DevOps Server

Optional. Leave `AZURE_DEVOPS_URL` and `AZURE_DEVOPS_PAT` empty to stay GitLab-only. GitLab webhook routes stay GitLab-only.

1. In `.env` set the TFS application root and a PAT, then restart Creasy:

   ```env
   AZURE_DEVOPS_URL=https://tfs02.company.com.tr/tfs
   AZURE_DEVOPS_PAT=<pat>
   AZURE_WEBHOOK_USER=creasy
   AZURE_WEBHOOK_PASSWORD=<independent-of-WEBHOOK_SECRET>
   ```

   On-prem TFS: `https://<server>/tfs` is enough. Creasy reads the
   collection from the webhook or PR URL. A collection URL still
   works. Do not use a project or `_git` URL.  
   The PAT needs **Code (Read)** and **Pull Request Threads (Read & write)**.  
   `AZURE_WEBHOOK_PASSWORD` is not `WEBHOOK_SECRET`; the two providers can run together.
2. In the Azure project: **Project settings → Service hooks → Create subscription**.
3. Service: **Web Hooks**.
4. Create **one subscription per event**, all with the same URL
   `http://<creasy-host>:9001/creasy/webhook/azure`:

   | Service Hook event | What Creasy does |
   |---|---|
   | Pull request created | Enqueue a review if the PAT user is already a reviewer |
   | Pull request commented | `@<bot> /ask` or `/review`; mention without a command posts usage |
   | Pull request updated | Ignored for new commits; assigning the PAT user starts a review; **abandoned** cancels jobs and deletes the clone |
   | Pull request merge attempted | Cancel jobs and delete the clone |

5. On each subscription’s action page:
   - **URL:** `http://<creasy-host>:9001/creasy/webhook/azure` — never a GitLab path.
   - **Basic authentication username / password:** `AZURE_WEBHOOK_USER` and `AZURE_WEBHOOK_PASSWORD`.  
     If the password is set, a missing or wrong `Authorization` header is **401**.
   - Resource: the repo to review, or all repos in the project.
6. Save and **Test** the created-PR subscription. Startup logs `azure_enabled=True` when the URL and PAT are set.

Empty `@<bot> /ask` is ignored. Draft MRs and PRs skip auto review when `SKIP_DRAFT_MRS=true`; an explicit `@<bot> /ask` or reviewer assign still runs.
`@<bot>` is the GitLab username / Azure display name of the token user.
Set `REVIEW_MENTION=creasy,Creasy Bot` to accept extra aliases.

## Triggers

Same commands on a GitLab merge request or an Azure pull request.

| Event | Action |
|---|---|
| MR / PR open (created) | Enqueue a review only if the token user or a `REVIEW_MENTION` alias is already a reviewer |
| Token user assigned or re-requested as reviewer | Enqueue a review |
| MR / PR update (new commits) / reopen | Ignored — assign or re-request the bot to run again |
| Comment `@<bot> /ask …` | Follow-up reply on that thread only (no new diff threads) |
| Comment `@<bot> /review` | Full review, or a focused review when the comment is a thread reply |
| Comment `@<bot>` without `/ask` or `/review` | Usage note on that thread (no OpenCode) |
| Comment `/ask` or `/review` alone | Ignored |
| MR close / merge, or Azure abandon / complete | Cancel jobs and delete the local clone |

A comment job replies on that comment’s thread when GitLab or Azure
sends a discussion/thread id. Auto-open reviews still post the
Overview on the MR/PR. If the thread reply fails, Creasy posts the
Overview instead.

OpenCode is told the merge-base and `git diff --stat`. It is **not** given the full unified diff; it inspects the tree from the separation point itself.

## Tests

```bash
pytest
```

Replay a fake webhook (defaults to `test_project` MR !30):

```bash
python tests/mock_gitlab_webhook.py --event mr-comment --note "@creasy /ask why this lock?"
python tester/tester.py
```

Tester UI: http://127.0.0.1:8090/ — pick a repo / MR and fire open, assign reviewer, `@creasy /ask`, close.
