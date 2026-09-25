# GitHub, then Render

Concrete steps. Roughly 30 minutes, most of it waiting for the worker image to build.

---

## 1. Push to GitHub

**A bundle is a clone source, not a file to upload.** Adding one through GitHub's web
interface stores a binary blob and the project does not appear. The quickest correct route is:

```bash
python tools/push_to_github.py --bundle foci-screen-ready.bundle
```

It verifies the bundle, clones it, repoints the remote at GitHub, checks the push is additive
rather than a rewrite, and pushes. Add `--dry-run` to see all of that and stop before the push.
The push itself needs an interactive GitHub sign-in, so it has to run in your terminal.

GitHub Desktop works just as well: **File → Add local repository**, pick the clone, then
**Push origin**. Use *Add*, not *Clone* — the folder is already a repository with the remote set.

The manual equivalent, and what the rest of this section assumes:

* `foci-screen.bundle` — a git bundle carrying the full history. Clone it:

  ```bash
  git clone foci-screen.bundle foci-screen
  cd foci-screen
  git remote remove origin          # points at the bundle file, not GitHub
  ```

* or the release zip, which is the same files without the history. Then `git init && git add -A
  && git commit`.

The first commit was audited before it was made: 54 files, no `.env`, no `*.db`, no
`credentials.json`, no local editor config, and no release archive. Verify for yourself with
`git log --stat -1`. If anything sensitive ever does appear, fix it *before* pushing — a secret
in a pushed commit stays in the history even if the next commit deletes it.

Create the repository on GitHub (new, empty, **private**, no README or .gitignore — the repo
has both), then:

```bash
git remote add origin git@github.com:<you>/foci-screen.git
git push -u origin main
```

**Keep it private.** The repository contains a working method for identifying and emailing
federal contracting officers about named companies. Public is a decision to make deliberately,
not by default.

Pushing runs `.github/workflows/ci.yml`, which is where the Docker images get built and booted
for the first time — see [CI](#ci-is-the-first-real-image-build) below. **Wait for it to pass
before touching Render.** A red CI run tells you what is wrong in two minutes; a failed Render
deploy takes longer to read.

**Before the first deploy, check the install.** The Docker images have not been built during
development. The next best thing is to install the package exactly as the images do — normal
install, not editable — and boot it:

```bash
python -m venv /tmp/foci-check
/tmp/foci-check/bin/pip install ".[api,queue,postgres]"
/tmp/foci-check/bin/python tools/install_smoke.py
```

It should report 17 of 17. It catches missing web assets, broken entrypoints, the health check
and fail-closed auth. It cannot catch OS-level problems in the images themselves (system
libraries, Chromium, file permissions) — that is what CI is for.

### CI is the first real image build

No container runtime was available while this was written, so until the first push the two
Dockerfiles are unverified. `.github/workflows/ci.yml` closes that gap, and the image jobs are
the point of it:

| Job | What it proves |
|---|---|
| `test` | Lint, 188 tests, and the install smoke on Python 3.12 — the version the images use |
| `api-image` | The API image builds, boots, serves `/health` and the web UI, and refuses an unauthenticated call |
| `worker-image` | The worker image builds **and Chromium actually launches as the unprivileged user** |

That last one is the check worth having. `playwright install --with-deps` runs as root and the
image then drops to uid 10001; if the browser or its libraries are unreadable afterwards, every
investor-relations page silently goes unread in production while the worker looks healthy.

If CI is red, read it before deploying. Most likely causes, in order: a missing system library
in the worker image, the web assets not shipping in the wheel (`[tool.setuptools.package-data]`),
and an extra that does not resolve on Python 3.12.

---

## 2. Deploy to Render

**New → Blueprint**, pick the repository. Render reads `render.yaml` and proposes five
resources: Postgres, Redis, the API, the worker, and the nightly cron job.

It will prompt for the variables marked `sync: false`. Set at minimum:

| Variable | Value |
|---|---|
| `FOCI_USER_AGENT` | `foci-screen/0.2 (you@yourorg.gov)` — SEC blocks generic agents |
| `FOCI_API_KEYS` | `yourteam:<random>` — see below |
| `FOCI_EMAIL_REDIRECT_TO` | your own address, for the whole pilot |

Generate a key:

```bash
python -c "import secrets; print('yourteam:' + secrets.token_urlsafe(32))"
```

The part before the colon is a **tenant**, and data is scoped to it. The CLI writes as
`default`, so if you also run screens locally against the same database and want to see them in
the web UI, name the tenant `default`.

Set the same `FOCI_USER_AGENT` on both the API and the worker. `DATABASE_URL` and `REDIS_URL`
are wired automatically.

Then open `https://<your-api>.onrender.com` in a browser — the web application is served by the
same service. Set your key once from **API key** in the top right; it is kept in that browser's
local storage and sent as a bearer token, so use a private profile on a shared machine.

To check the service itself:

```bash
curl https://<your-api>.onrender.com/health
```

```json
{"status":"ok","version":"0.8.0","queue":"rq","database":"postgres",
 "authenticated":true,"warnings":[]}
```

Every field matters. `"queue":"thread"` means Redis did not connect and jobs will die with
the web process; `"database":"sqlite"` means `DATABASE_URL` did not take and the data is on a
disk Render discards; `"authenticated":false` means `FOCI_API_KEYS` did not take and every
authenticated route is returning 503.

`warnings` is the same three checks in plain English, and an empty list is the only good
answer. The web UI shows anything in it as a banner across the top of every page, so a
deployment that is quietly throwing its data away says so on screen rather than waiting to be
asked.

---

## 3. First screen

```bash
export FOCI_KEY=<the key you generated>
export API=https://<your-api>.onrender.com

curl -X POST $API/v1/screens \
  -H "Authorization: Bearer $FOCI_KEY" -H "Content-Type: application/json" \
  -d '{"agency":"Department of Defense","sub_agency":"Department of the Navy",
       "months_back":6,"max_awards":25,"max_entities":5}'
```

Returns `{"run_id":"...","status":"queued"}`. Poll it:

```bash
curl -H "Authorization: Bearer $FOCI_KEY" $API/v1/screens/<run_id>
```

Expect minutes, not seconds. The first run against a fresh database is a **baseline** — it
establishes what "normal" looks like, and its findings are damped accordingly. The second run
onward is where the value is.

Then review what it wants to send:

```bash
curl -H "Authorization: Bearer $FOCI_KEY" "$API/v1/notices?status=pending"
curl -H "Authorization: Bearer $FOCI_KEY" $API/v1/notices/<id>.eml -o notice.eml
```

---

## 4. Schedule it

```bash
curl -X POST $API/v1/watchlists \
  -H "Authorization: Bearer $FOCI_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Navy quarterly","screen":{"agency":"Department of Defense",
       "sub_agency":"Department of the Navy","months_back":3,"max_entities":10}}'
```

The cron job sweeps active watchlists nightly at 07:00 UTC. **A failed cron run is a real
alarm.** The scheduler exits 2 when it cannot reach a queue, so Render marks the run failed
instead of showing green while nothing is screened. Check that `REDIS_URL` is wired to the
cron service.

---

## "API is closed" on Render

`FOCI_API_KEYS` is marked `sync: false` in the blueprint, which means Render **asks** for it
when the blueprint is applied and leaves it empty if the prompt is skipped. An empty value is
the fail-closed state: the service starts, answers `/health`, and refuses everything else.

Fix it on the service rather than in the repository — it is a secret:

1. Render → the **`foci-api`** service → **Environment**
2. Add `FOCI_API_KEYS` with a value like `default:sk_...`
3. **Save changes**; the service restarts on its own

Only the API service reads it. The worker and cron job do not.

Confirm from outside without a key:

```bash
curl https://<your-api>.onrender.com/health
```

`"authenticated": true` means keys are loaded. `false` means the variable is still empty.

Then open the site, click **API key**, and paste the key *without* the tenant prefix — the
`default:` part names the tenant, not the key. (A value with no prefix at all is accepted and
treated as the `default` tenant, so this is hard to get wrong now.)

## "The site loads but shows nothing"

Almost always one of two things, and they look identical in the browser:

**No API keys.** `FOCI_API_KEYS` is unset, so every authenticated route returns 503 and the
page has nothing to render. The server says so loudly on startup now, and `GET /health`
reports `"authenticated": false` without needing a key. Fix it by generating one:

```bash
python -c "import secrets; print('default:sk_' + secrets.token_urlsafe(24))"
```

Put that in `FOCI_API_KEYS` — a `.env` file beside the package locally, or the service's
environment on Render — and restart. Use the tenant `default` if you also run screens from the
CLI, which writes as that tenant.

**No data yet.** A key opens the API, but a fresh database is empty. Run a screen
(`foci-screen screen --agency ...`, or `POST /v1/screens`) before expecting the dashboard to
show anything. An empty database and a closed API are different problems with the same symptom,
so the overview now says which one it is instead of drawing eight charts of zeros.

## Fixing a single web service, without the blueprint

**New → Web Service** on the repository gets you a running site and none of its backing
resources. It looks fine. `/health` reports what is actually missing:

```json
{"queue":"thread","database":"sqlite","authenticated":true,"warnings":["…"]}
```

That deployment keeps its database in the container's filesystem, which Render replaces on
every deploy, every restart and every scale event. Snapshots are the baseline the change
detection compares against — losing them means the next run sees every document as new, which
is both a wave of false findings and the loss of the only thing that would have caught a real
change.

The blueprint is not the only way out, and the three steps below are worth doing in order.
**Step 1 is the one that matters.** Stopping after it leaves a deployment that keeps its data
and screens correctly; steps 2 and 3 buy robustness, and cost money.

### On the free plan, read this first

The free instance type decides most of it:

* **No disks.** Route A below is not available. Use Postgres.
* **It spins down after about 15 minutes with no inbound request.** A screen running in the
  web process goes with it, mid-run. Keep screens small (`max_entities` of 3–5), or better,
  use the CLI route at the end of this section, which does not depend on the instance staying
  up at all.
* **A free Postgres instance expires after 30 days.** Render deletes it. Put a reminder in
  your calendar now: when it goes, the snapshot baseline goes with it, and the next screen
  reads every document as new.

A screen that dies with its instance used to sit at `running` for good, because nothing was
left alive to write the closing row. The API now closes those out on startup — they read
`interrupted`, with the reason — so a stuck screen is distinguishable from a slow one.

### 1. Somewhere the data survives a restart

Two routes. Both fix the same thing; pick on what your service can do.

**Route A — a persistent disk.** One service, no new resource, two settings. This is the
shortest path if the blueprint is not available to you, **and it needs a paid instance type.**

1. The service → **Settings** → **Disks** → **Add Disk**
2. Mount path `/var/data`, size 1 GB
3. **Environment** → add `FOCI_DB` = `/var/data/foci_screen.db`
4. **Save changes**; the service restarts on its own

Render requires a paid instance type for a disk, and a service with one runs a single instance
and loses zero-downtime deploys. Neither matters here: the store holds one connection per
process by design, and a screening tool has no traffic to speak of.

**If the service is on the free instance type, this route is not available** — free instances
have no disks, and they also spin down when idle, which is its own wipe. Use Route B.

`/health` should then report `"ephemeral_storage": false`.

**Route B — Postgres.** More moving parts, works on any plan, and the one to pick if you will
ever add a worker: two processes cannot share a SQLite file, but they can share a database.

1. **New → Postgres**. `basic-256mb` if you can; the free tier works and expires at 30 days,
   which is a date to diarise rather than a reason not to start. Same region as the web
   service.
2. Copy its **Internal Database URL**.
3. On the web service: **Environment → Add Environment Variable**, `DATABASE_URL` = that URL.
4. **Save changes.** The service restarts by itself.

`curl https://<your-api>.onrender.com/health` should now say `"database":"postgres"`, and the
SQLite warning should be gone from `warnings`.

If instead you see a warning about `psycopg`, the service was built without the Postgres
extra. It is a build-command problem, not a database problem: `Dockerfile` installs
`.[api,queue,postgres]`, so a Docker service already has the driver. A native Python service
needs its build command set to `pip install ".[api,queue,postgres]"`.

Nothing from the SQLite copy carries across. That costs nothing if no screen has run, and the
data was going to be discarded anyway.

### 2. A queue, so a screen survives the web process

With `queue: thread`, `POST /v1/screens` runs the screen inside the web service in a
background thread. It works, and it is how the CLI has always run. What it cannot do is
survive a restart, a redeploy or an instance being recycled mid-run — and a screen takes
minutes.

1. **New → Key Value**, then set `REDIS_URL` on the web service to its internal URL.
2. **New → Background Worker**, same repository, runtime Docker, dockerfile path
   `./Dockerfile.worker`, plan `standard` (Chromium OOMs on `starter`), with the same
   `DATABASE_URL`, `REDIS_URL` and `FOCI_USER_AGENT`.

Do both or neither. `REDIS_URL` without a worker is worse than no queue at all: screens are
accepted, queue up, and nothing ever runs them — `/health` says `queue: rq` and looks
healthier than the state it replaced.

### 3. The nightly sweep

**New → Cron Job**, same repository and Dockerfile as the API, command
`python -m foci_screen.scheduler`, schedule `0 7 * * *`, with `DATABASE_URL`, `REDIS_URL` and
`FOCI_USER_AGENT`. It exits 2 when it cannot reach a queue, so a failed run shows as failed
rather than green-while-screening-nothing.

### Or: screen from your own machine, and let Render display it

**On the free plan this is the recommended way to run screens**, not a fallback. The web
instance spins down while idle and takes any in-process screen with it; your own machine does
not. Point the CLI at the same Postgres and Render becomes purely the place the results are
read — no worker, no queue, and nothing that has to stay awake for minutes at a time:

```bash
pip install ".[postgres]"
export DATABASE_URL="<the EXTERNAL connection string from Render>"
foci-screen screen --agency "Department of Defense" --months 6 --entities 5
```

Three things to know. The **external** URL is the one that works from outside Render, and it
carries a password — keep it out of the repository and out of your shell history. Your
network has to allow outbound 5432. And the CLI writes as tenant `default`, so the key you
use in the browser must be `default:<key>` or the site will not show what you just screened.

A screen run this way is identical to one the worker would have run; the only difference is
which machine spends the minutes.

## Costs and gotchas

- **If Render rejects `type: redis` in the blueprint, change it to `keyvalue`.** Render renamed
  the product to Key Value and accepts both spellings at the time of writing, but this
  blueprint has not been applied against a live account — the rest of it cross-checks
  (every `fromDatabase` and `fromService` reference resolves), but the schema itself is
  unverified.
- **The worker needs `standard`.** Chromium OOMs on `starter`. This is the main running cost
  and the reason the browser is in its own service.
- **Free Postgres expires at 30 days.** The blueprint asks for `basic-256mb`. Losing the
  database loses the baseline, and every document then reads as new on the next run — the
  alert storm the change-detection design exists to prevent.
- **The worker image is large** (~1.2GB, Chromium and its libraries). First build is slow;
  later ones are cached. The API image is unaffected, which is why they are separate.
- **`FOCI_CACHE` points at `/tmp`.** Render's disk is ephemeral. The HTTP cache is a
  per-deploy convenience; nothing durable depends on it. Snapshots live in Postgres.
- **Some investor-relations sites will not be read.** The crawler obeys `robots.txt`,
  identifies itself, and does not get around bot management. Hosts such as
  `investors.lockheedmartin.com` refuse it, and runs say so in their notes. If the worker's
  Chromium cost isn't earning its keep for your watchlists, `FOCI_BROWSER=false` on the worker
  lets you drop it to `starter`.

## Before you let it mail anyone

`GMAIL_ENABLED` and `GMAIL_SEND` are both `false` in the blueprint, so approving a notice
records a decision and produces a downloadable `.eml` — it transmits nothing. That is the
intended steady state.

If you do arm it, arm drafting only (`GMAIL_ENABLED=true`, `GMAIL_SEND=false`) and leave
`FOCI_EMAIL_REDIRECT_TO` pointed at yourself until you have read a month of notices and agree
with them. These go to federal officials about named companies; the failure modes are subtle,
and the person best placed to catch one is whoever reads the draft.
