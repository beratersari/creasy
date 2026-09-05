# Creasy

Code review easy. GitLab webhooks trigger a deep OpenCode review of a merge request against the cloned codebase.

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

Clones live with the MR. They are deleted only when the MR is closed or merged. Each `/review` or `/ask` is a separate job that can resume the same OpenCode session. `/reset` deletes that MR’s comments posted by the `GITLAB_TOKEN` user and drops the stored session; it does not call OpenCode.

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
Webhook: `POST /webhook`  
Health: `GET /health`

CI uploads one folder per pack (`creasy-0.1.0-windows-x64`, `linux-x64`, `darwin`, `windows-linux`). GitHub wraps each folder as a zip; the download is not a zip of a zip. Each pack includes bundled CPython, matching wheels, the OpenCode CLI, and the built dashboard (`web/dist`). No Node on the target. Unzip, `install`, `install-opencode`, `start`.

Point a GitLab project webhook at `/webhook` with merge request events and comments. Secret must match `WEBHOOK_SECRET`.

## Triggers

| Event | Action |
|---|---|
| MR open / reopen | Enqueue a review |
| MR update with new commits (`oldrev`) | Enqueue a review unless that MR is already busy |
| MR comment `/review …` | Full review job (queued FIFO if one is running) |
| MR comment `/ask …` | Follow-up on the same `ses_*` |
| MR comment `/reset` | Delete notes and threads authored by the token user; clear `ses_*`. No OpenCode |
| MR close / merge | Cancel jobs and delete the local clone |

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
