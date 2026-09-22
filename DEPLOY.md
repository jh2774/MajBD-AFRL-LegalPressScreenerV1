# GitHub, then Render

Concrete steps. Roughly 30 minutes, most of it waiting for the worker image to build.

---

## 1. Push to GitHub

`.gitignore` already excludes `.env`, `credentials.json`, `token.json`, `*.db` and `out/`.
Check that it worked before the first push rather than after:

```bash
git init
git add -A
git status                    # read this list; no .env, no *.db, no credentials
```

If anything sensitive appears, stop and fix `.gitignore` first — a secret in the first commit
stays in the history even if the next commit deletes it.

```bash
git commit -m "foci-screen: contract FOCI/IP screening service"
gh repo create foci-screen --private --source=. --push
```

**Keep it private.** The repository contains a working method for identifying and emailing
federal contracting officers about named companies. Public is a decision to make deliberately,
not by default.

**Before the first deploy, check the install.** The Docker images have not been built during
development. The next best thing is to install the package exactly as the images do — normal
install, not editable — and boot it:

```bash
python -m venv /tmp/foci-check
/tmp/foci-check/bin/pip install ".[api,queue,postgres]"
/tmp/foci-check/bin/python tools/install_smoke.py
```

It should report 16 of 16. It catches missing web assets, broken entrypoints, the health check
and fail-closed auth. It cannot catch OS-level problems in the images themselves (system
libraries, Chromium, file permissions); if the first Render build fails, look there first.

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
{"status":"ok","queue":"rq","database":"postgres","authenticated":true}
```

All four fields matter. `"queue":"thread"` means Redis did not connect and jobs will die with
the web process; `"authenticated":false` means `FOCI_API_KEYS` did not take and every
authenticated route is returning 503.

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

## Costs and gotchas

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
