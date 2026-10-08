# Runbook — de-track `config.yaml` on the deployed dispatcher

**Issue:** THU-27 · **Author:** HomelabOps · **Status:** ready for Mathijs to execute

This runbook migrates the deployed Qbittorrent-Dispatcher from "`config.yaml` is
tracked in git and is also the live config file" to "`config.yaml` is gitignored,
`config.example.yaml` is tracked". The mount path does not change.

Run it yourself on the Docker host. HomelabOps has no live access and has not
touched the host.

---

## Why this needs a runbook and not a bare `git rm --cached`

The repo change alone is safe. Pulling it onto the host is not, for two reasons:

1. **`git pull` deletes the file.** The commit removes `config.yaml` from the
   tree. If the host's working copy is clean, the pull deletes the live config
   with your real credentials in it. (If the host copy has uncommitted edits,
   git instead aborts the merge — safer, but it leaves the checkout wedged.)
2. **Docker then creates a directory.** On the next `docker compose up`, a
   bind-mount source that does not exist is created by Docker as an empty
   **directory**. The container starts, fails to parse `/app/config.yaml`, and
   restarts forever. This is the actual outage mechanism.

Step 1 takes a backup outside the checkout precisely so neither of these can
cost you the config.

## Lenses

- **Blast radius** — the dispatcher only. No Home Assistant, no Proxmox host, no
  Jellyfin. While it is down, Sonarr/Radarr submissions to it fail; qBittorrent
  keeps seeding and existing downloads keep running. Nobody in the house
  notices unless a grab is queued during the window. Expected downtime: under a
  minute at step 6.
- **Rollback path** — one step, from a known-good state: `git checkout main` on
  the host plus restoring the step-1 backup returns you exactly to today's
  state. Full procedure in [Rollback](#rollback).
- **Secrets out of the repo** — this is the point of the change.
- **Config as code** — `config.example.yaml` is now the tracked schema of
  record; the live file is data, not code.
- **Single point of failure** — the live config exists in exactly one place (the
  host file). Step 1's backup is what stops that from being a new SPOF, and
  [Follow-up](#follow-up-after-this-lands) proposes removing it properly.

## Before you start

- Branch to merge: `thuis/THU-27-detrack-live-config`
- You need: shell on the Docker host, and the directory of the dispatcher
  checkout. Everything below assumes you have `cd`'d into it.
- Time: about 10 minutes.
- Money cost: **€0**. No new service, no new container, no extra disk beyond one
  small text backup.
- Do **not** paste any value out of `config.yaml` into the Paperclip ticket.

Set these once so the commands below can be copy-pasted:

```bash
export QBD_DIR=/path/to/Qbittorrent-Dispatcher   # the checkout on the host
export QBD_BACKUP=$HOME/qbd-config-backup        # NOT inside the checkout
cd "$QBD_DIR"
```

---

## Step 1 — Back up the live config outside the checkout

This is the step that makes every later step reversible. Do not skip it.

```bash
mkdir -p "$QBD_BACKUP"
chmod 700 "$QBD_BACKUP"
cp -a config.yaml "$QBD_BACKUP/config.yaml.$(date +%Y%m%d-%H%M%S)"
chmod 600 "$QBD_BACKUP"/config.yaml.*
ls -l "$QBD_BACKUP"
```

**Expected:** one file, `-rw-------`, non-zero size, named with today's date.

**If it differs:**
- `cp: cannot stat 'config.yaml'` → the live config is not in this directory.
  Find the real bind-mount source before continuing:
  `docker inspect qb-dispatcher --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'`
  Set `QBD_DIR`/paths to match, then redo this step.
- Size is 0 → stop. Do not continue. Report on THU-27.

## Step 2 — Record whether this is already an incident

THU-27 lists one unknown: does the host's tracked `config.yaml` hold real
credentials? These two commands answer it without printing any secret.

```bash
# Is this working copy a git checkout at all?
git rev-parse --is-inside-work-tree 2>/dev/null || echo "NOT A GIT CHECKOUT"

# Does the live file differ from what is committed? (names only, no values)
git status --porcelain -- config.yaml
git diff --stat -- config.yaml
```

**Expected (the good case):** `true`, then `git status` prints a line for
`config.yaml` and `git diff --stat` shows it changed — meaning the live file has
your real values and they were **never committed**. Nothing is exposed.

**If it differs:**
- `NOT A GIT CHECKOUT` → the live config was never at risk from `git add`. The
  repo change still lands; skip to step 4 and ignore steps 5–6's pull.
- Both commands print **nothing** (file identical to HEAD) → the live file holds
  only the committed placeholders. Either this is not the live deployment, or
  the dispatcher is running on placeholder credentials. Note which on THU-27.

Report the one-word outcome of this step on THU-27. Do not paste the diff.

## Step 3 — Confirm no real credential was ever committed

Already verified by HomelabOps from the public repo history, and worth
re-confirming on the host because it decides whether a history rewrite is
needed:

```bash
git log --all --format='%H' -- config.yaml \
  | while read c; do git show "$c:config.yaml" 2>/dev/null; done \
  | grep -iE '^\s*(password|username|api_key|admin_api_key):' | sort -u
```

**Expected:** only placeholders — `u`, `p`, `admin`, `secret`,
`supersecretkey`, `YOUR_*_API_KEY`, `null`, `''`.

**If it differs:** a real credential is in the public history. Stop this runbook,
comment on THU-27 immediately, and treat it as credential rotation first — the
de-track does not help once a value is published.

## Step 4 — Merge the repo change

Review and merge `thuis/THU-27-detrack-live-config` on GitHub (Reviewer has it).
Nothing happens on the host until you pull in step 5.

## Step 5 — Pull onto the host, keeping the live config

The pull wants to delete `config.yaml`. Stash the live file first, pull, put it
back.

```bash
mv config.yaml ../qbd-config-live.tmp     # out of the checkout, briefly
git -c advice.detachedHead=false pull --ff-only origin main
mv ../qbd-config-live.tmp config.yaml
```

**Expected:** the pull fast-forwards with no conflict, then:

```bash
test -f config.example.yaml && echo "example present"
test -f config.yaml && echo "live config present"
git check-ignore -v config.yaml
git status --porcelain
```

should print `example present`, `live config present`, a `.gitignore` line
matching `config.yaml`, and **empty** `git status` output. Empty status is the
proof: the live config is now invisible to `git add -A`.

**If it differs:**
- `pull --ff-only` refuses → the host checkout has other local commits or
  changes. Do not force. `mv ../qbd-config-live.tmp config.yaml` to restore,
  then report on THU-27.
- `git status` still lists `config.yaml` → the ignore rule is not matching.
  **Do not run `git add -A`.** Check you are on the merged commit
  (`git log --oneline -1`) and report on THU-27.
- `config.yaml` missing after the final `mv` → restore from step 1:
  `cp -a "$QBD_BACKUP"/config.yaml.* config.yaml`

## Step 6 — Observability before change: capture a known-good baseline, then restart

Record what healthy looks like **before** you restart, so you can tell whether
the restart broke anything.

```bash
docker ps --filter name=qb-dispatcher --format '{{.Status}}'
curl -fsS -o /dev/null -w 'baseline HTTP %{http_code}\n' http://localhost:8001/health \
  || curl -fsS -o /dev/null -w 'baseline HTTP %{http_code}\n' http://localhost:8001/
```

**Expected:** an `Up ...` status and a `2xx`/`3xx` baseline code. Write the code
down. If the service is already unhealthy, fix that first — do not restart into
an unknown state.

Then apply the change:

```bash
docker compose up -d --build
sleep 10
docker ps --filter name=qb-dispatcher --format '{{.Status}}'
docker compose logs --tail=30 dispatcher
```

**Expected:** `Up` (not `Restarting`), and logs with no
`Configuration file not found` and no `Invalid config`.

**If it differs:**
- `Restarting` loop, or `Configuration file not found` → the bind mount is
  wrong. Check for the directory failure mode:
  `ls -ld config.yaml` — if it says `drwx` it is a **directory**. Then:
  `docker compose down && rmdir config.yaml && cp -a "$QBD_BACKUP"/config.yaml.* config.yaml && docker compose up -d`
- `Invalid config` → the YAML got mangled. Restore step 1's backup and restart.
- Build fails on `COPY config.example.yaml` → you are on a commit without the
  example file; confirm step 5's pull actually landed.

## Step 7 — Verify the service really works, not just that it started

```bash
curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8001/health \
  || curl -fsS -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8001/
```

**Expected:** the same code you recorded as baseline in step 6.

Then confirm the web configurator still reads and, critically, still **writes**
through the mount — that round-trip is the whole reason the file is mounted
read/write:

1. Open `http://<host>:8001/config` in a browser.
2. Confirm your real nodes and *arr instances are listed. **Expected:** the same
   entries as before the change.
3. Change something trivial and harmless (e.g. a node's `weight` from `1.0` to
   `1.1`) and save. **Expected:** the save succeeds with no error.
4. On the host: `grep -n 'weight' config.yaml` → **expected:** shows `1.1`,
   proving the container wrote through the mount to the host file.
5. Set it back to `1.0` and save. Then:

```bash
git status --porcelain
```

**Expected: empty.** This is the acceptance test for the whole change — the web
UI just wrote credentials to disk and git did not notice.

**If `git status` is not empty here**, the de-track failed. Do not commit
anything. Report on THU-27.

## Step 8 — Report

Comment on THU-27 with:

- the one-word result of step 2 (real values / placeholders only / not a checkout)
- the result of step 3 (placeholders only — yes/no)
- whether step 7's `git status` came back empty
- any step where the output differed from expected

No credential values in the comment. If you need to share one, put it in
Paperclip as a secret.

---

## Rollback

Safe at any point. The repo change and the host state roll back independently.

**On the host (restores today's behaviour exactly):**

```bash
cd "$QBD_DIR"
docker compose down
git checkout main                         # or: git reset --hard <pre-pull-sha>
cp -a "$QBD_BACKUP"/config.yaml.* config.yaml   # pick the newest
docker compose up -d --build
docker ps --filter name=qb-dispatcher --format '{{.Status}}'
```

**Expected:** `Up`, and step 7's health check returns your baseline code.

Note the pre-pull SHA before step 5 if you want the stricter form:
`git rev-parse HEAD`.

**In the repo:** revert the merge commit on `main`. `config.yaml` returns to
being tracked with placeholders only.

**What rollback does not undo:** nothing. No data is migrated, no volume is
touched, no credential is rotated or invalidated by this change.

## Destructive steps in this runbook — confirmation gate

Three commands can destroy the live config. Each is gated on step 1's backup
existing. **Before running any of them, confirm `ls -l "$QBD_BACKUP"` shows a
non-empty backup file.**

- `mv config.yaml ../qbd-config-live.tmp` (step 5) — moves the live config.
- `rmdir config.yaml` (step 6 recovery) — only ever run this when `ls -ld`
  confirms it is a directory Docker created. It refuses to delete a file, and it
  refuses to delete a non-empty directory. That is deliberate; do not reach for
  `rm -rf` instead. If `rmdir` fails, stop and report.
- `git reset --hard` (rollback) — discards host-local changes.

`docker compose down` stops the dispatcher but destroys no data; there are no
named volumes in `docker-compose.yml`.

## Follow-up after this lands

Not part of this change — one change, one proposal. Tracked separately:

- **Move the live config out of the git checkout entirely** (e.g.
  `/etc/qb-dispatcher/config.yaml`, mounted to the same container path). The
  gitignore makes an accidental commit hard; moving the file makes it
  impossible, and it survives re-cloning the repo. Needs a compose change and a
  one-time file move.
- **Back up the live config.** It now exists in exactly one place on one host
  and is not in git. Per the **restore, not backup** lens, that proposal needs a
  restore test, not just a copy job.
- **Rotate `admin_api_key` if it is still `supersecretkey`.** Step 2 will reveal
  this. The placeholder is in the public repo history, so if the deployment is
  using it, it is a published credential regardless of this change.
