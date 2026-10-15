# Moving FOCI-Screener to Google Cloud

> **Not the current plan.** The site is deployed on Vercel with Supabase —
> [docs/VERCEL.md](VERCEL.md) — and the Render site is retired, so there is nothing to move.
> This guide is kept for reference.

This moves the website from Render to **Cloud Run** and its database from Render's Postgres to
**Cloud SQL**. Nothing is deleted from Render until the last step, so at any point before then
the old site is still there, untouched, and you can go back to it ("Going back", near the end).

Plan on about an hour and a half, in one sitting. Roughly twenty minutes of that is waiting for
Google to create things.

> **Status of this guide.** Every command was checked against Google's documentation on
> 13 October 2026, and the application side is tested (see "What was changed in the code" at the
> end). The commands have still not been run against a live Google Cloud project, because that
> needs your account. Each step says what you should see; if a step shows something else, stop
> there rather than pressing on. The old site keeps working while you find out why.
>
> **Up to date with** pull requests #6 (a contractor's SAM registration read from FPDS) and #7
> (a screening policy for each watchlist), merged 15 October 2026. Neither needs a new setting
> or a step of its own; what they change for the move is in "Watchlists" and step 8. If more
> pull requests are merged before you start, push them and let Render deploy them first —
> "Before you start", step 1.

## The order, and why

1. **Pause alert checks on the old site.** From here until the switch, neither site checks or
   emails anything, so nothing is sent twice and nothing is used up without being sent.
2. **Build the new site**, paused as well, with an empty database.
3. **Copy the data** across.
4. **Check the new site** — that it can do the work, and that it holds exactly what the old
   one does.
5. **Switch**: suspend the old site, un-pause the new one, point the daily check at it.
6. A week later, **retire Render**.

Do not run a screen on the old site once you have started. Anything it records after the copy
stays behind.

## What moves where

| Today, on Render | On Google Cloud | Notes |
|---|---|---|
| Web service | **Cloud Run** service | Same container, built from the same `Dockerfile` |
| Postgres database | **Cloud SQL** for PostgreSQL | Data is copied across by a command in this repository |
| Environment settings | Cloud Run settings, with passwords and keys in **Secret Manager** | |
| Deploy when you push | **Cloud Build** trigger on the GitHub repository | Step 10 |
| Daily alert check | The same GitHub workflow, pointed at the new address | Or Cloud Scheduler; step 11 |
| Watchlists, and each one's screening policy | Copied with the rest of the database | Not screened nightly on either host; see "Watchlists" |

**What does not move, because it is not on Render either:** the patent-lien index
(`patent_assignments.db`) is a file on whichever computer built it. A website on Render's free
plan or on Cloud Run has no disk to keep it on, so screens run from the site say that recorded
liens were not checked, exactly as they do today. Screens run from your own computer against
the database are unaffected.

**Cost.** About $10 a month, nearly all of it the database, which runs all the time.
Everything else fits in Google's free allowances at this tool's level of use. "What it costs,
and keeping it down", near the end, has the breakdown and the four things that keep it there.
Unlike Render's free database, Cloud SQL is not deleted after 30 days and keeps daily backups.

## Before you start

**1. Get the current code onto the old site.** Push from GitHub Desktop (and merge any pull
requests you mean to include), wait for the checks on the repository's **Actions** tab to go
green, and wait for Render to finish deploying. The old site needs the same version as the new
one: it has the pause switch and the System check page the steps below use, and starting up
brings its database to the same shape — pull request #7, for one, added a column for each
watchlist's policy. Both databases must have the same shape for the copy to be exact.

To confirm it has arrived: the old site's footer shows a **System check** link, and the top
line of that page shows the commit Render built, as seven characters (for example `250a418`).
It should match the newest commit on the repository's GitHub page.

**2. Open the old site's System check** (the link in the footer, or add `#/system` to its
address). Keep that tab open for the whole move. You need two things from it:

* **Settings in use → To carry to a new site.** Every name listed there needs the same value on
  the new site. The values themselves are on Render → your service → **Environment**. Expect at
  least `FOCI_API_KEYS` and `FOCI_USER_AGENT`; `BREVO_API_KEY` and `ALERTS_FROM` if email is
  connected; possibly `SAM_API_KEY` or `USPTO_API_KEY`.
* **What is stored here** — the row counts. Step 8 compares them with the new site's.

**3. Copy Render's database address.** Render → your **database** → **Connect** → **External
Database URL**. It starts with `postgresql://` and contains the database password.

**4. On Google's side**, a Google account with a **billing account** (console.cloud.google.com
→ Billing), and a **project**: the project menu at the top → **New project**. Give it a name,
make sure it is linked to your billing account, and note its **Project ID** (shown under the
name; it is not always the same as the name).

Everything in grey boxes below is typed into **Cloud Shell**: on console.cloud.google.com, the
terminal icon `>_` at the top right. It is a command line in your browser with Google's tools
already installed. Paste each box and press Enter.

## 0. Pause alert checks on the old site

On Render → your service → **Environment**, add a variable `ALERTS_PAUSED` with the value
`true`, and save. Render redeploys, which takes a few minutes.

**You should see** a banner across the top of the old site: *"Alert checks are paused on this
site…"*. From now on it checks nothing and emails nothing. Then press **Check again** on its
System check tab, so the row counts you compare later are from after the pause.

## 1. Settings

Change the first line to your Project ID. **If Cloud Shell is closed and reopened, paste this
box again first** — it forgets these between sessions.

```bash
export PROJECT_ID="your-project-id"
export REGION="us-central1"              # Iowa: among Google's cheapest. us-east1 is too.
export SERVICE="foci-screener"
export INSTANCE="foci-db"
export CONN="$PROJECT_ID:$REGION:$INSTANCE"
export RUN_SA="foci-run@$PROJECT_ID.iam.gserviceaccount.com"
gcloud config set project "$PROJECT_ID"
```

Then switch on the Google services this uses (a minute or so):

```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com cloudscheduler.googleapis.com compute.googleapis.com
```

(The last one is not used to run anything. Switching it on creates the account that Google's
build service works as, which step 4 gives a permission to.)

## 2. The database

This creates the smallest Postgres instance, with daily backups and protection against being
deleted by accident. **It takes five to ten minutes** and prints nothing until it is done.

```bash
gcloud sql instances create "$INSTANCE" \
  --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro \
  --region="$REGION" --storage-size=10 --storage-auto-increase \
  --backup-start-time=08:00 --deletion-protection
```

If Google refuses `db-f1-micro`, use `--tier=db-g1-small` instead (the next size, about three
times the price). Do not add `--no-assign-ip`: the way Cloud Run reaches the database in this
guide needs the instance's public address, which is the default. That address accepts no
connections from the internet unless you add some, and this guide adds none.

Then a database inside it and a user for the application. You will be asked to choose a
password; nothing shows as you type. You do not need to remember it — the next step stores it.

```bash
gcloud sql databases create foci --instance="$INSTANCE"
read -rs -p "Choose a database password: " DB_PASSWORD; echo
gcloud sql users create foci --instance="$INSTANCE" --password="$DB_PASSWORD"
```

## 3. Passwords and keys

These go into Secret Manager rather than into settings anyone on the project can read. Each
line asks for one value; paste it and press Enter. Nothing shows as you paste.

```bash
printf '%s' "$DB_PASSWORD" | gcloud secrets create foci-db-pass --data-file=-
unset DB_PASSWORD

secret() { read -rs -p "$2: " V; echo; printf '%s' "$V" | gcloud secrets create "$1" --data-file=-; unset V; }
secret foci-api-keys "FOCI_API_KEYS, exactly as on Render"
secret render-db-url "Render's External Database URL"
```

If email alerts are connected, also:

```bash
secret brevo-api-key "BREVO_API_KEY"
```

And one more line like it for any other key on the old site's "to carry" list — for example
`secret sam-api-key "SAM_API_KEY"`. Step 5 says where each one is plugged in.

## 4. Permissions

The website runs as its own account, allowed to do two things: reach the database and read
those secrets. The last command lets Google's build service deploy what it builds.

```bash
gcloud iam service-accounts create foci-run --display-name="FOCI-Screener on Cloud Run"
for ROLE in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$RUN_SA" --role="$ROLE" --condition=None
done
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" --role=roles/run.builder --condition=None \
  --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
```

If the last command says that account does not exist, wait a minute — it is created shortly
after step 1 — and paste the last two lines again.

## 5. The website

Fetch the code and write the non-secret settings to a file. **Edit the `FOCI_USER_AGENT` line**
to your own value from Render before pasting — the SEC refuses requests that do not carry a
real contact address. `ALERTS_PAUSED` keeps the new site from checking or emailing anything
until step 9.

```bash
cd ~ && git clone https://github.com/jh2774/MajBD-AFRL-LegalPressScreenerV1.git foci && cd foci
cat > ~/foci-env.yaml <<EOF
INSTANCE_CONNECTION_NAME: "$CONN"
DB_USER: "foci"
DB_NAME: "foci"
FOCI_INPROCESS_SCREENS: "true"
ALERTS_PAUSED: "true"
FOCI_USER_AGENT: "FOCI-Screener (your.name@your-organisation.com)"
EOF
```

Any other *non-secret* name on the old site's "to carry" list goes in that file the same way —
one line, `NAME: "value"`. (`ALERTS_FROM` waits until step 9.)

Build and start it. If it asks to create a place to keep the built image, answer `y`. **About
five minutes.**

```bash
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated \
  --service-account "$RUN_SA" --add-cloudsql-instances "$CONN" \
  --env-vars-file ~/foci-env.yaml \
  --set-secrets "DB_PASS=foci-db-pass:latest,FOCI_API_KEYS=foci-api-keys:latest" \
  --memory 1Gi --cpu 1 --max-instances 1 --timeout 900
```

If you made a secret for another key in step 3, add it inside the quotes after `--set-secrets`,
separated by a comma: for example `,SAM_API_KEY=sam-api-key:latest`.

Why those last four settings:

* `--max-instances 1` — the application keeps some things in the one running copy of itself.
  One copy is the supported arrangement. (The thing that would do harm with more — two alert
  checks sending the same email — is guarded in the database as well.)
* `--memory 1Gi` — reading the largest Form ADV filings peaks at about 230 MB. The default of
  512 MB works but leaves little room.
* `--timeout 900` — how long one request may take. A long alert check or screen is not limited
  by it: the site keeps itself awake for those (see "What is different on Google Cloud").
* `--allow-unauthenticated` — the site is reachable from the internet, as it is on Render, and
  protected by its API key. If Google refuses this with a message about organisation policy,
  your organisation blocks public services and an administrator has to allow it.

Tell the site its own address, which it puts in alert emails and uses to keep itself awake
during long work:

```bash
export URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "FOCI_PUBLIC_URL=$URL"
echo "$URL"
```

## 6. Check the new site, still empty

```bash
curl -s "$URL/health"
```

**You should see**, among other things:

* `"host":"Cloud Run"` — this is the new site answering.
* `"database":"postgres"` — it found Cloud SQL. **If this says `sqlite`, stop**: the database
  settings did not take, and anything saved now would be lost. Re-check step 5.
* `"paused":true`, and under `"warnings"` only the one about alert checks being paused.

**Do not sign in and use the new site yet.** The copy in the next step needs it empty.

## 7. Copy the data

The application copies its own data: it reads every table from Render and writes it to Cloud
SQL, then compares the row counts on both sides. Render's database is only read, never changed.
It runs as a one-off job on Google's side, using the website's own image.

```bash
IMAGE=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(spec.template.spec.containers[0].image)')
gcloud run jobs create foci-copy --image "$IMAGE" --region "$REGION" \
  --service-account "$RUN_SA" --set-cloudsql-instances "$CONN" \
  --set-env-vars "INSTANCE_CONNECTION_NAME=$CONN,DB_USER=foci,DB_NAME=foci,FOCI_USER_AGENT=foci-copy" \
  --set-secrets "DB_PASS=foci-db-pass:latest,SOURCE_DATABASE_URL=render-db-url:latest" \
  --command foci-screen --args copy-db \
  --max-retries 0 --task-timeout 30m --memory 1Gi
gcloud run jobs execute foci-copy --region "$REGION" --wait
```

Read what it said:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="foci-copy"' \
  --freshness=1h --order=asc --limit=100 --format='value(textPayload)'
```

**You should see**, as the last line, **`Done. … every table's count matches the source.`**,
above a line per table. (The same output is on console.cloud.google.com → Cloud Run → Jobs →
foci-copy → Logs.)

If instead it says:

* **`Not started: The destination already holds rows`** — something was saved on the new site
  before the copy. If that was only you trying it out, the simplest fix is to delete and
  recreate the database (`gcloud sql databases delete foci --instance="$INSTANCE"`, then the
  `databases create` line from step 2) and run the `execute` line again.
* **`INCOMPLETE`** — it lists the tables that came up short. Run the `execute` line again; a
  copy that was interrupted is safe to repeat. If it still comes up short, stop and go back
  ("Going back").
* a connection error naming Render — the External Database URL was mistyped. Replace it with
  `printf '%s' 'the-url' | gcloud secrets versions add render-db-url --data-file=-` and execute
  again.

## 8. Check the new site, with the data

Open the address from step 5 in your browser.

1. Click **API key** and enter your key. The browser remembers it per website address, so the
   new address starts without it.
2. Open **System check** (the link in the footer). **Every line should say "working" or
   "note".** A line that says "not working" tells you what failed and how to fix it; fix it
   and press **Check again** before going on. The ones that matter most on a new host:
   * *Database* — a row was written, read back and removed.
   * *The site can reach its own address* — this is how a long screen or alert check keeps
     running on Cloud Run.
   * The four *SEC* lines, *USAspending* and *FPDS* — Google's network can reach them, and the
     SEC accepts your `FOCI_USER_AGENT`. FPDS matters more since pull request #6: a screen now
     reads each contractor's SAM registration from it, with no SAM key.
   * *Watchlists* — a note, not a fault. It says how many are saved; see "Watchlists".
3. **Compare "What is stored here" with the old site's tab.** The row counts should be the same,
   table for table. The quickest way: copy the one-line version from each page and paste them
   under one another. A table that differs was not fully copied — go back to step 7.
4. The **Overview** should show the same contractors and awards as the old site.
5. On **Portfolio**, paste your portfolio key (the `.foci-key.txt` file you saved). Your
   dashboard opens, and your email alert list appears with it — the page recognises the list
   saved for that portfolio, so do not start a new one.

Nothing is checked or emailed while you do this: the new site is still paused.

## 9. Switch over

Do these together, in this order.

1. **Suspend the old site.** Render → the service → Settings → **Suspend**. (Suspended is not
   deleted. It can be resumed.)
2. **Email, if connected.** Give the new site the mail settings:
   ```bash
   gcloud run services update "$SERVICE" --region "$REGION" \
     --update-secrets "BREVO_API_KEY=brevo-api-key:latest" \
     --update-env-vars "ALERTS_FROM=the-address-you-confirmed-in-brevo"
   ```
   Then on the new site's Portfolio page, **Send a test email**. A test goes out even while
   checks are paused.
3. **Un-pause the new site.**
   ```bash
   gcloud run services update "$SERVICE" --region "$REGION" --remove-env-vars ALERTS_PAUSED
   ```
   **You should see** the banner about paused checks gone from the new site, and on its
   Portfolio page **Check now** should run and report what it checked. Anything that changed
   while both sites were paused is found and sent now, once.
4. **The daily check.** On GitHub → the repository → Settings → Secrets and variables → Actions
   → **Variables**, change `FOCI_SITE_URL` to the new address. See step 11 for the alternative.
5. **Tell people the new address.** They enter the API key once, and paste their portfolio key
   once.

## 10. Deploy when you push

On Render, pushing from GitHub Desktop redeploys the site. To get the same here:
console.cloud.google.com → **Cloud Run** → click the service → **Set up continuous
deployment** (it may be worded "Connect to repo"). Choose GitHub, sign in, pick
`MajBD-AFRL-LegalPressScreenerV1`, branch `main`, build type **Dockerfile**, and save. Each push
to `main` then builds and deploys, keeping every setting from step 5.

Until that is set up, a deploy by hand is:

```bash
cd ~/foci && git pull && gcloud run deploy "$SERVICE" --source . --region "$REGION"
```

## 11. The daily check

The site checks for changes whenever someone opens the Portfolio page and the last check was
most of a day ago. For days nobody does, something has to ask it. Either of these; having both
does no harm, because two checks never run at once.

* **Keep the GitHub workflow** — nothing to do beyond step 9.4.
* **Or Cloud Scheduler**, which keeps everything on Google:
  ```bash
  read -rs -p "Your API key (the part after 'default:'): " KEY; echo
  gcloud scheduler jobs create http foci-daily-alerts --location "$REGION" \
    --schedule "15 12 * * *" --time-zone "Etc/UTC" \
    --uri "$URL/v1/alerts/run" --http-method POST \
    --headers "X-API-Key=$KEY,Content-Type=application/json" --message-body '{}' \
    --attempt-deadline 30m
  unset KEY
  ```
  The key is stored in the scheduler job, where people with access to the project can read it.

## 12. Retire Render

Give it a week of the new site working. Then on Render delete the web service and the database,
and in Google Cloud remove what was only needed for the move:

```bash
gcloud run jobs delete foci-copy --region "$REGION"
gcloud secrets delete render-db-url
```

## Going back

Until step 12 the old site and its database are intact.

* **Before step 9**, nothing has changed for anyone. On Render, delete the `ALERTS_PAUSED`
  variable and save; the old site carries on as before. You can try the move again another day
  from step 5 or 7 — delete and recreate the Cloud SQL database first (step 7's first bullet),
  so the copy starts from empty.
* **After step 9**, resume the Render service, delete its `ALERTS_PAUSED` variable, set
  `FOCI_SITE_URL` on GitHub back to the Render address, and pause the new site
  (`gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars
  ALERTS_PAUSED=true`). What was screened or sent from the new site after the switch is not on
  the old one, so alerts sent in that time may be sent again by the old site.

## What it costs, and keeping it down

What this guide sets up, and roughly what each costs a month. Prices are approximate, from
Google's published rates and free allowances in October 2026; Google's pricing calculator has
the current figures.

| Google service | What it is for | About |
|---|---|---|
| **Cloud SQL** for PostgreSQL, `db-f1-micro`, 10 GB SSD, daily backups | The database | **$9–10** |
| **Cloud Run** | The website | $0 — inside the monthly free allowance (2 million requests, 50 hours of processor time) |
| **Cloud Build** | Building the site from the code | $0 — 2,500 free build-minutes a month; a deploy takes about five |
| **Artifact Registry** | Keeping the built site | $0 — 0.5 GB free, if old copies are cleared (below) |
| **Secret Manager** | Passwords and keys | $0 — 6 free; this guide uses 3 to 5 |
| **Cloud Logging** | The site's log | $0 — 50 GB free |
| The daily check | Kept on GitHub (step 11) | $0 |

The database is the only thing that runs all the time, which is why it is nearly the whole
bill. `db-f1-micro` is Google's smallest; Google offers no free database. New Google Cloud
accounts get $300 of credit for their first 90 days, which covers this many times over.

Four things keep it there:

1. **The region.** `us-central1` (Iowa), the guide's default, or `us-east1` (South Carolina) are
   among Google's cheapest. Others, Northern Virginia included, cost a little more.
2. **Leave Cloud Run's defaults alone.** No minimum number of instances, and the default billing
   (processor time only while answering requests). The site then costs nothing while nobody is
   using it. Step 5's settings are already right.
3. **Keep the daily check on GitHub** rather than Cloud Scheduler (step 11). Both work; GitHub's
   is free and already set up.
4. **Clear old copies of the site.** Each deploy stores a new copy (an "image") of a few hundred
   MB, and only 0.5 GB is free. console.cloud.google.com → **Artifact Registry** → the
   `cloud-run-source-deploy` repository → **Edit** → **Cleanup policies** → add a policy that
   keeps the **2 most recent versions**, and save.

And one safety net, free: console.cloud.google.com → **Billing** → **Budgets & alerts** →
**Create budget**, for $15 a month, with email alerts at 50%, 90% and 100%. A budget does not
stop anything; it emails you if the bill heads somewhere unexpected.

After the move, step 12 deletes the copy job and one secret that are no longer needed.

## Watchlists

A watchlist is a saved screen — an agency or a contractor, with its options — and since pull
request #7 each one can carry its own screening policy: what to screen for and what raises a
notice, laid over the site's own policy. Watchlists and their policies live in the database, so
step 7 copies them with everything else. Nothing needs setting for them.

**What does not change: nothing runs them on a schedule, on Render today or on Google Cloud as
set up here.** The nightly sweep (`foci-scheduler`) hands each watchlist to a job queue
(`REDIS_URL`) and a separate worker, and refuses to run without them. The site on Render is a
single web service with neither, and so is this Cloud Run setup. A watchlist is screened when
someone runs it (`POST /v1/watchlists/{id}/run`), and that screen runs inside the site and keeps
it awake like any other. The System check's *Watchlists* line says how many are saved.

If watchlists should be screened every night on Google Cloud, that is a change to make after
the move, not part of it: the sweep can be given a way to run its screens itself, as a Cloud Run
job started by Cloud Scheduler, which needs no queue or worker. Ask for it when it is wanted.

## What is different on Google Cloud

Three things about Cloud Run are unlike a server that is simply always on. The application
handles each; this is so you know what you are looking at.

* **The site sleeps when nobody is using it, and wakes on the next visit.** The first page
  after a quiet spell takes a few seconds. That is the same as Render's free plan, and it is
  why the site costs so little.
* **It only gets processor time while it is answering a request.** A screen runs in the
  background for minutes after the page that started it has loaded, and an alert check can
  outlast whoever asked for it — in the weeks investment firms refile, there are dozens of new
  forms to read. So while either is running, the site keeps a request open to itself. Without
  that the work would slow to a crawl, or be cut off when Google decided the site was idle. You
  pay for the minutes the work takes and no others. A screen still in progress when you deploy
  a new version is cut off and marked *interrupted*, as before; what it had finished is kept.
* **There is no disk.** Files the site writes are held in its memory and vanish when it sleeps.
  Everything that matters is in Cloud SQL; the only thing written to "disk" is a cache of
  responses from government sites, which the application keeps under 64 MB.

And one about the database: a connection left idle can be closed by Google. The application
checks a connection that has been idle before using it and reconnects if it has gone, so this
shows up as nothing at all rather than as a failed page.

## Looking at what the site is doing

* **System check**, in the site's footer: everything it depends on, tried for real, and what
  it holds.
* Health, without signing in: `curl -s "$URL/health"`
* Recent log lines: `gcloud run services logs read "$SERVICE" --region "$REGION" --limit 50`
* The same in the browser: console.cloud.google.com → Cloud Run → the service → **Logs**.
* Change a setting: `gcloud run services update "$SERVICE" --region "$REGION"
  --update-env-vars "NAME=value"`. It restarts with the new value; the others are kept.

## What was changed in the code for this

* **Database address.** Cloud SQL's four separate values (`INSTANCE_CONNECTION_NAME`,
  `DB_USER`, `DB_NAME`, `DB_PASS`) are accepted as well as a single `DATABASE_URL`, so the
  password is one secret and never has to be worked into a URL by hand.
* **Lost connections.** The database connection is checked after sitting idle and replaced if it
  has been closed; a read that hits a dead connection is repeated once on a new one.
* **Staying awake for long work.** Described above, for screens and for alert checks. It also
  helps on Render's free plan, where long work could be cut off when the instance went idle.
* **One alert check at a time, across copies.** The guard against two checks sending the same
  email twice holds in the database, not only inside one running copy.
* **`ALERTS_PAUSED`.** Stops a site checking altogether. Holding alerts as drafts would not do
  for a move: a draft counts as said, so a new site left on drafts while it was being tried
  would use up alerts nobody received.
* **System check.** A page (and `GET /v1/system/check`) that tries each dependency for real and
  counts every table, so a new site can be shown to work and to hold what the old one holds.
  It includes FPDS, which screens rely on for SAM registrations since pull request #6, and says
  how many watchlists are saved and whether anything screens them on a schedule.
* **Cache size.** Expired cached responses are deleted and the cache is capped.
* **`foci-screen copy-db`.** The command step 7 runs.
* **`/health`** names the host that answered and the deploy — Cloud Run's revision, or the
  commit Render built — to tell the two sites apart during the move and to show the old one is
  current, and says whether alert checks are paused.
* **The alert list follows the portfolio** to a new website address, so re-saving does not
  create a second list.

Tested: the application side by the test suite, including a run that boots the container the
way Cloud Run does (its port, its environment, Postgres) and a run of the copy, the
reconnection and the lock against a real Postgres server. Those two run on GitHub when you
push; check they are green on the repository's **Actions** tab before step 7.
