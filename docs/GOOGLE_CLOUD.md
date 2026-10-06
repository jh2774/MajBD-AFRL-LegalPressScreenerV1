# Moving FOCI-Screener to Google Cloud

This moves the website from Render to **Cloud Run** and its database from Render's Postgres to
**Cloud SQL**. Nothing is deleted from Render until the last step, so at any point before then
the old site is still there, untouched, and you can stop and go back to it.

Plan on about an hour. Roughly twenty minutes of that is waiting for Google to create things.

> **Status of this guide.** The commands were written from Google's current documentation and
> the application's own behaviour, and the application side is tested (see "What was changed
> in the code" at the end). The commands themselves have not been run against a live Google
> Cloud project. Each step says what you should see; if a step shows something else, stop
> there rather than pressing on.

## What moves where

| Today, on Render | On Google Cloud | Notes |
|---|---|---|
| Web service | **Cloud Run** service | Same container, built from the same `Dockerfile` |
| Postgres database | **Cloud SQL** for PostgreSQL | Data is copied across by a command in this repository |
| Environment settings | Cloud Run settings, with passwords and keys in **Secret Manager** | |
| Deploy when you push | **Cloud Build** trigger on the GitHub repository | Set up in step 10 |
| Daily alert check | The same GitHub workflow, pointed at the new address | Or Cloud Scheduler; step 11 |

**Cost.** Roughly $10–15 a month, nearly all of it the database, which runs all the time. The
website costs little or nothing at light use: Cloud Run charges for the seconds it is handling a
request and has a monthly free allowance. These are estimates; Google's pricing calculator has
the current figures. Unlike Render's free database, Cloud SQL is not deleted after 30 days and
keeps daily backups.

## Before you start

Have these to hand. All of them are on Render → your service → **Environment**, except the
last:

1. The value of `FOCI_API_KEYS` (it looks like `default:` followed by your key).
2. The value of `FOCI_USER_AGENT`.
3. If email alerts are connected: `BREVO_API_KEY` and `ALERTS_FROM`.
4. Render → your **database** → **Connect** → **External Database URL**. It starts with
   `postgresql://` and contains the database password.

And on Google's side:

5. A Google account with a **billing account** (console.cloud.google.com → Billing).
6. A **project**: console.cloud.google.com → the project menu at the top → **New project**.
   Give it a name, and make sure it is linked to your billing account. Note its **Project ID**
   (shown under the name; it is not always the same as the name).

Everything below is typed into **Cloud Shell**: on console.cloud.google.com, the terminal icon
`>_` at the top right. It is a command line in your browser with Google's tools already
installed. Paste each grey block and press Enter.

## 1. Settings

Change the first line to your Project ID. **If Cloud Shell is closed and reopened, paste this
block again first** — it forgets these between sessions.

```bash
export PROJECT_ID="your-project-id"
export REGION="us-east4"                 # Northern Virginia. us-central1 (Iowa) also works.
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
  --region="$REGION" --storage-size=10GB --storage-auto-increase \
  --backup-start-time=08:00 --deletion-protection
```

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

Fetch the code and write the non-secret settings to a file. **Edit the last line** to your own
`FOCI_USER_AGENT` from Render before pasting — the SEC refuses requests that do not carry a real
contact address.

```bash
cd ~ && git clone https://github.com/jh2774/MajBD-AFRL-LegalPressScreenerV1.git foci && cd foci
cat > ~/foci-env.yaml <<EOF
INSTANCE_CONNECTION_NAME: "$CONN"
DB_USER: "foci"
DB_NAME: "foci"
FOCI_INPROCESS_SCREENS: "true"
FOCI_USER_AGENT: "FOCI-Screener (your.name@your-organisation.com)"
EOF
```

Build and start it. The first time, it asks to create a place to keep the built image — answer
`y`. **About five minutes.**

```bash
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated \
  --service-account "$RUN_SA" --add-cloudsql-instances "$CONN" \
  --env-vars-file ~/foci-env.yaml \
  --set-secrets "DB_PASS=foci-db-pass:latest,FOCI_API_KEYS=foci-api-keys:latest" \
  --memory 1Gi --cpu 1 --max-instances 1 --timeout 900
```

Why those last four settings:

* `--max-instances 1` — the application keeps some things in the one running copy of itself.
  One copy is the supported arrangement. (The two things that would do harm with more — two
  alert checks sending the same email — are guarded in the database as well.)
* `--memory 1Gi` — reading the largest Form ADV filings peaks at about 230 MB. The default of
  512 MB works but leaves little room.
* `--timeout 900` — an alert check that finds several changed filings can take a few minutes.
* `--allow-unauthenticated` — the site is reachable from the internet, as it is on Render, and
  protected by its API key. If Google refuses this with a message about organisation policy,
  your organisation blocks public services and an administrator has to allow it.

Tell the site its own address, which it puts in alert emails:

```bash
export URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "FOCI_PUBLIC_URL=$URL"
echo "$URL"
```

## 6. Check the new site, still empty

```bash
curl -s "$URL/health"
```

You should see, among other things:

* `"host":"Cloud Run"` — this is the new site answering.
* `"database":"postgres"` — it found Cloud SQL. **If this says `sqlite`, stop**: the database
  settings did not take, and anything saved now would be lost. Re-check step 5.
* `"warnings":[]`

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

The last line should be **`Done. … every table's count matches the source.`**, above a line per
table. (The same output is on console.cloud.google.com → Cloud Run → Jobs → foci-copy → Logs.)

If instead it says:

* **`Not started: The destination already holds rows`** — something was saved on the new site
  before the copy. If that was only you trying it out, the simplest fix is to delete and
  recreate the database (`gcloud sql databases delete foci --instance="$INSTANCE"`, then the
  `databases create` line from step 2) and run the `execute` line again.
* **`INCOMPLETE`** — it lists the tables that came up short. Run the `execute` line again; a
  copy that was interrupted is safe to repeat. If it still comes up short, stop and keep using
  Render.
* a connection error naming Render — the External Database URL was mistyped. Replace it with
  `printf '%s' 'the-url' | gcloud secrets versions add render-db-url --data-file=-` and execute
  again.

## 8. Check the new site, with the data

Open the address from step 5 in your browser.

1. Click **API key** and enter your key. The browser remembers it per website address, so the
   new address starts without it.
2. The **Overview** should show the same contractors and awards as the old site.
3. On **Portfolio**, paste your portfolio key (the `.foci-key.txt` file you saved). Your
   dashboard opens, and your email alert list appears with it — the page recognises the list
   saved for that portfolio, so do not start a new one.

## 9. Switch over

Do these together, in this order, so that the two sites are never both sending alerts.

1. **Stop the old site checking and sending.** Render → the service → Settings → **Suspend**.
   (Suspended is not deleted. It can be resumed.)
2. **Email, if connected.** Give the new site the mail settings:
   ```bash
   gcloud run services update "$SERVICE" --region "$REGION" \
     --update-secrets "BREVO_API_KEY=brevo-api-key:latest" \
     --update-env-vars "ALERTS_FROM=the-address-you-confirmed-in-brevo"
   ```
   Then on the Portfolio page, **Send a test email**. Google does not block the ports ordinary
   email uses the way Render's free plan does (only port 25), but there is no reason to change
   a mail setup that works.
3. **The daily check.** On GitHub → the repository → Settings → Secrets and variables → Actions,
   change the variable `FOCI_SITE_URL` to the new address. See step 11 for the alternative.
4. **Tell people the new address.** They enter the API key once, and paste their portfolio key
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

* **Keep the GitHub workflow** — nothing to do beyond step 9.3.
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

## What is different on Google Cloud

Three things about Cloud Run are unlike a server that is simply always on. The application
handles each; this is so you know what you are looking at.

* **The site sleeps when nobody is using it, and wakes on the next visit.** The first page
  after a quiet spell takes a few seconds. That is the same as Render's free plan, and it is
  why the site costs so little.
* **It only gets processor time while it is answering a request.** A screen runs in the
  background for minutes after the page that started it has loaded, so while one is running the
  site keeps a request open to itself. Without that the screen would slow to a crawl, or be cut
  off when Google decided the site was idle. You pay for the minutes a screen takes and no
  others. A screen still in progress when you deploy a new version is cut off and marked
  *interrupted*, as before; what it had finished is kept.
* **There is no disk.** Files the site writes are held in its memory and vanish when it sleeps.
  Everything that matters is in Cloud SQL; the only thing written to "disk" is a cache of
  responses from government sites, which the application now keeps under 64 MB.

And one about the database: a connection left idle can be closed by Google. The application
checks a connection that has been idle before using it and reconnects if it has gone, so this
shows up as nothing at all rather than as a failed page.

## Looking at what the site is doing

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
* **Staying awake for a screen.** Described above. It also helps on Render's free plan, where a
  long screen could be cut off when the instance went idle.
* **One alert check at a time, across copies.** The guard against two checks sending the same
  email twice now holds in the database, not only inside one running copy.
* **Cache size.** Expired cached responses are deleted and the cache is capped.
* **`foci-screen copy-db`.** The command step 7 runs.
* **`/health`** names the host that answered and the deploy, to tell the two sites apart during
  the move.
* **The alert list follows the portfolio** to a new website address, so re-saving does not
  create a second list.

Tested: the application side by the test suite, including a run that boots the container the
way Cloud Run does (its port, its environment, Postgres) and a run of the copy, the
reconnection and the lock against a real Postgres server. Those two run on GitHub when you
push; check they are green on the repository's **Actions** tab before step 7.
