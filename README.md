# Creasy

Code review easy. GitLab webhooks trigger a deep OpenCode review of a merge request against the cloned codebase.

Agent rules and commit conventions: [AGENTS.md](AGENTS.md).

Clones live with the MR. They are deleted only when the MR is closed or merged. Each `/review` or `/ask` is a separate job that can resume the same OpenCode session.

## Run

Offline (recommended on a locked-down host):

```bash
# On a machine with network — download wheels
scripts\vendor.bat          # Windows
scripts/vendor.sh           # Linux / macOS

# Copy the repo including vendor/python-wheels to the target host, then:
install.bat                 # or scripts/install.bat
# edit .env (GITLAB_TOKEN, WEBHOOK_SECRET, OPENCODE_MODEL)
start.bat                   # or scripts/start.bat
```

Online / from a checkout:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
# set GITLAB_TOKEN, WEBHOOK_SECRET, OPENCODE_MODEL
python -m creasy
```

Dashboard: http://127.0.0.1:8000/jobs  
Webhook: `POST /webhook`  
Health: `GET /health`

CI uploads platform zips (`creasy-offline-linux`, `creasy-offline-windows`) on each `main` run. Download the matching artifact, unzip, then `install` + `start`. OpenCode is not in the zip.

Point a GitLab project webhook at `/webhook` with merge request events and comments. Secret must match `WEBHOOK_SECRET`.

## Triggers

| Event | Action |
|---|---|
| MR open / reopen | Enqueue a review |
| MR update with new commits (`oldrev`) | Enqueue a review unless that MR is already busy |
| MR comment `/review …` | Full review job (queued FIFO if one is running) |
| MR comment `/ask …` | Follow-up on the same `ses_*` |
| MR close / merge | Cancel jobs and delete the local clone |

OpenCode is told the merge-base and `git diff --stat`. It is **not** given the full unified diff; it inspects the tree from the separation point itself.

## Tests

```bash
pytest
```

Replay a fake webhook:

```bash
python tests/mock_gitlab_webhook.py --event mr-comment --note "/ask why this lock?"
```
