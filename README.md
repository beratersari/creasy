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

Clones live with the MR or PR. They are deleted only when it is closed, merged, or abandoned. Each `/review` or `/ask` is a separate job that can resume the same OpenCode session. `/reset` deletes that review’s comments posted by the token user and drops the stored session; it does not call OpenCode.

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
GitLab webhook: `POST /webhook`  
Azure webhook: `POST /webhook/azure`  
Health: `GET /health`

The product version is the `VERSION` file (`/health` and `/api/meta` expose it).
Bump with `python scripts/bump_version.py minor`. What changed is in
[`CHANGELOG.md`](CHANGELOG.md).

Download **GitHub Release** executable zips
(`creasy-<version>-windows-x64.zip`, `linux-x64`, `darwin-arm64`),
not the tag’s “Source code” zip. Each zip is one `creasy` /
`creasy.exe` plus `.env.example`. Copy the example to `.env` next
to the binary and run it. Reviews still need `opencode` on `PATH`.

## Webhooks

The dashboard cannot start a review. GitLab and Azure each post to
their own URL. Creasy must be reachable from the GitLab or Azure
host, not only from your laptop.

### GitLab

1. In the project or group: **Settings → Webhooks**.
2. **URL:** `http://<creasy-host>:9001/webhook`  
   Use `https://` if Creasy is behind TLS. Do not use `/webhook/azure`.
3. **Secret token:** the same value as `WEBHOOK_SECRET` in `.env`.  
   GitLab sends it as `X-Gitlab-Token`. A missing or wrong secret is **401**.
4. Enable these triggers only:
   - **Merge request events** — `open` starts a review; `close` / `merge` cancel jobs and delete the clone; `update` and `reopen` are ignored.
   - **Comments** — first command token wins: `/review`, `/ask <question>`, `/reset`.
5. If Creasy is plain HTTP or uses an intercept certificate, leave **Enable SSL verification** unchecked.
6. Save, then **Test** with a Merge request hook. Creasy should answer immediately (`accepted`, `queued`, or `ignored`).

`GITLAB_TOKEN` needs the `api` scope so Creasy can clone over HTTPS, post the overview note, and open diff threads. Use a dedicated bot user: notes from that user are ignored, and `/reset` deletes only that user’s notes and threads.

### Azure DevOps Server

Optional. Leave `AZURE_DEVOPS_URL` and `AZURE_DEVOPS_PAT` empty to stay GitLab-only. GitLab `/webhook` is unchanged.

1. In `.env` set the collection URL and a PAT, then restart Creasy:

   ```env
   AZURE_DEVOPS_URL=https://ado.example/tfs/DefaultCollection
   AZURE_DEVOPS_PAT=<pat>
   AZURE_WEBHOOK_USER=creasy
   AZURE_WEBHOOK_PASSWORD=<independent-of-WEBHOOK_SECRET>
   ```

   The PAT needs **Code (Read)** and **Pull Request Threads (Read & write)**.  
   `AZURE_WEBHOOK_PASSWORD` is not `WEBHOOK_SECRET`; the two providers can run together.
2. In the Azure project: **Project settings → Service hooks → Create subscription**.
3. Service: **Web Hooks**.
4. Create **one subscription per event**, all with the same URL
   `http://<creasy-host>:9001/webhook/azure`:

   | Service Hook event | What Creasy does |
   |---|---|
   | Pull request created | Enqueue a review |
   | Pull request commented | `/review`, `/ask`, `/reset` (other comments ignored) |
   | Pull request updated | Ignored for new commits; **abandoned** cancels jobs and deletes the clone |
   | Pull request merge attempted | Cancel jobs and delete the clone |

5. On each subscription’s action page:
   - **URL:** `http://<creasy-host>:9001/webhook/azure` — never `/webhook`.
   - **Basic authentication username / password:** `AZURE_WEBHOOK_USER` and `AZURE_WEBHOOK_PASSWORD`.  
     If the password is set, a missing or wrong `Authorization` header is **401**.
   - Resource: the repo to review, or all repos in the project.
6. Save and **Test** the created-PR subscription. Startup logs `azure_enabled=True` when the URL and PAT are set.

Empty `/ask` is ignored. Draft MRs and PRs skip auto review when `SKIP_DRAFT_MRS=true`; an explicit `/review`, `/ask`, or `/reset` still runs.

## Triggers

Same commands on a GitLab merge request or an Azure pull request.

| Event | Action |
|---|---|
| MR / PR open (created) | Enqueue a review |
| MR / PR update (new commits) / reopen | Ignored — comment `/review` to run again |
| Comment `/review …` | Full review job (queued FIFO if one is running) |
| Comment `/ask …` | Follow-up on the same `ses_*` |
| Comment `/reset` | Delete notes and threads authored by the token user; clear `ses_*`. No OpenCode |
| MR close / merge, or Azure abandon / complete | Cancel jobs and delete the local clone |

OpenCode is told the merge-base and `git diff --stat`. It is **not** given the full unified diff; it inspects the tree from the separation point itself.

## Tests

```bash
pytest
```

Replay a fake webhook (defaults to `test_project` MR !30):

```bash
python tests/mock_gitlab_webhook.py --event mr-comment --note "/ask why this lock?"
python tester/tester.py
```

Tester UI: http://127.0.0.1:8090/ — pick a repo / MR and fire open, `/review`, `/ask`, `/reset`, close.
