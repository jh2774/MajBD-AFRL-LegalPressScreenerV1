# Deploying FOCI-Screener on Vercel, with Supabase

The website runs on **Vercel** as one Python function, and its database is **Supabase**
Postgres, connected to the Vercel project. This is a fresh start: nothing is copied from an
earlier site.

About 20 minutes. Everything is done in a browser: vercel.com, supabase.com (through Vercel)
and github.com.

## What you need

* A **Vercel** account (Hobby, free). Sign in with GitHub so Vercel can see the repository.
* Access to the GitHub repository `jh2774/MajBD-AFRL-LegalPressScreenerV1`, with the latest
  code pushed.
* Two values you make up now and keep somewhere safe:
  * an **API key** for the site: a long random string, say 40 letters and digits from a
    password manager's generator;
  * a **contact line for the SEC**, which refuses requests without one:
    `FOCI-Screener (your.name@your-organisation.com)`.

## 1. Create the Vercel project

1. vercel.com → **Add New…** → **Project** → find `MajBD-AFRL-LegalPressScreenerV1` →
   **Import**.
2. Leave **Framework Preset** as Vercel detects it (FastAPI) and the root directory as `./`.
3. Open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `FOCI_API_KEYS` | `default:` followed by your API key — for example `default:k3J9…` |
   | `FOCI_USER_AGENT` | your SEC contact line |

4. **Deploy.** The build takes a minute or two.

**You should see** the build succeed. The site works at this point, but has no database yet:
every page shows a banner saying the database "will not survive a restart". That is
expected, and the next step fixes it.

If the build fails with *No FastAPI entrypoint found*, the code Vercel built is older than
this guide. Push the latest commit from GitHub Desktop; Vercel builds again on its own.

## 2. Connect Supabase

1. In the Vercel project → **Storage** → **Create Database** (or **Browse Marketplace**) →
   **Supabase** → **Continue**.
2. Choose the **Free** plan. Choose the region **East US (North Virginia)**, which is next to
   where Vercel runs the site (Washington, D.C.). Give it a name, such as `foci-screener`.
   **Create**.
3. When asked which environments to connect, keep **Production** (Preview and Development
   too, if offered) and leave any **custom prefix** empty. **Connect**.

Vercel adds the database's settings to the project for you: `POSTGRES_URL`,
`POSTGRES_URL_NON_POOLING` and others. You don't need to copy or edit any of them; the site
works out which to use.

4. Redeploy so the site picks them up: **Deployments** → the newest one → **⋯** → **Redeploy**.

## 3. Check it

Open the site at its Vercel address (Vercel shows it on the project page; it ends in
`.vercel.app`).

1. Click **API key** at the top right and paste your API key — the part *after* `default:`.
2. Open **System check** (the link at the bottom of every page). Every line should say
   **working** or **note**. In particular:
   * **Database** should read *Postgres database postgres at aws-…pooler.supabase.com, tables
     in schema foci. A row was written, read back and removed.*
   * the **SEC**, **IAPD**, **USAspending** and **FPDS** lines show Vercel can reach them, and
     that the SEC accepts your contact line.
   * **Screens** is a note about the time limit (below).

   A line that says **not working** says what failed and how to fix it.
3. The banner about the database is gone.

## 4. The daily alert check

Email alerts are checked whenever someone opens the Portfolio page and the last check was most
of a day ago. For days nobody does, a GitHub workflow in the repository asks the site once a
day. Point it at the new site:

GitHub → the repository → **Settings** → **Secrets and variables** → **Actions**:

* **Variables** tab → `FOCI_SITE_URL` → your Vercel address, e.g.
  `https://majbd-afrl-legalpressscreenerv1.vercel.app` (no slash at the end). Create it if it
  is not there; change it if it still points at the old Render site.
* **Secrets** tab → `FOCI_ALERTS_KEY` → your API key (the part after `default:`).

To try it: **Actions** → **Daily alerts** → **Run workflow**.

## 5. Optional

* **Sending email.** Without a mail service the site writes each alert and shows it on the
  Portfolio page, but sends nothing. To send: in Vercel → **Settings** → **Environment
  Variables**, add `BREVO_API_KEY` and `ALERTS_FROM` (DEPLOY.md, "Turning on email alerts",
  has the Brevo steps), then redeploy. **Send a test email** on the Portfolio page confirms
  it.
* **Links in alert emails** use the address the check was started from. To fix them to one
  address, add `FOCI_PUBLIC_URL` = your Vercel address.
* **Other keys** you may have — `SAM_API_KEY`, `USPTO_API_KEY` — go in the same place.
* **Supabase's Data API.** This site does not use it, and keeps its tables in a schema the
  Data API does not publish (`foci`). To switch the Data API off altogether: in Supabase,
  open the project → **Integrations** → **Data API** → turn **Enable Data API** off.
* **Seeing the tables in Supabase.** Table Editor → change the schema at the top from
  `public` to `foci`.

## Starting from an empty database

Nothing comes across from the old site. Screen the agencies or contractors you need from the
**Screening** page. A **portfolio key** file saved from the old site still works: paste it on
the Portfolio page and the companies come back. Their history on the old site does not, and
an email alert list has to be set up again.

## Limits of the free plans

**Vercel Hobby**

* **A screen stops after 4 minutes** and keeps what it finished. Vercel ends any request at 5
  minutes on this plan, and a screen runs inside the request the page sends to start it. Up to
  about 15 contractors usually fit; screen more in several goes.
* **Keep the page open while a screen runs.** Moving to another page of the site is fine.
  Closing or reloading the tab ends the screen; what it finished is kept, and it is marked
  *interrupted* a few minutes later.
* **An alert check** also has 5 minutes. If a big one is cut off, the next check carries on
  from where it got to, because everything it reads is saved as it goes.
* Vercel's terms limit Hobby to personal, non-commercial use. Commercial use needs Pro
  ($20/month per developer), which also allows screens of up to about 13 minutes: set
  `FOCI_SCREEN_SECONDS` to `770` and `maxDuration` in `vercel.json` to `800`.

**Supabase Free**

* **500 MB of database.** Enough for a long time at this tool's size; the Supabase dashboard
  shows how much is used.
* **Paused after a week without activity.** The daily check should keep it active. If it
  ever pauses, the site shows database errors: open the project on supabase.com and choose
  **Restore**.
* **No automatic backups** on the free plan.

## If something goes wrong

* **Logs:** Vercel → the project → **Logs**.
* **The database line in System check is not working:**
  * *password authentication failed*: the database password was changed in Supabase.
    Disconnect and reconnect the store in Vercel → **Storage**, then redeploy.
  * *Max client connections reached*: too many copies of the site are holding connections at
    once. It clears by itself within minutes.
* **Everything returns 503:** `FOCI_API_KEYS` is missing. Add it and redeploy.

## How the site handles Vercel and Supabase

These are already in the code; this section explains what you will see.

* **Which database address.** Supabase offers a direct address, which only works over IPv6,
  and a pooled one in two modes. Vercel cannot reach IPv6. The pooler's *transaction* mode
  would break three things this site relies on: the lock that stops two alert checks running
  at once, prepared statements, and the schema setting. So the site uses the pooler's
  **session mode** (port 5432), whichever of Supabase's addresses it was given. An address
  set by hand as `DATABASE_URL` takes precedence and gets the same correction.
* **Its own schema.** On Supabase every table goes in the schema `foci` rather than `public`,
  so Supabase's Data API does not publish them. `FOCI_DB_SCHEMA` chooses another; an empty
  value means `public`.
* **Screens inside requests.** Vercel gives a function no processor time once its response
  is sent, so a screen cannot run in the background. The page that starts a screen sends a
  second request that runs it, and the screen's own page shows its progress meanwhile.
  `FOCI_SCREEN_SECONDS` sets the limit (240 by default on Vercel).
* **Files** go to `/tmp`, the only place a Vercel function may write; nothing is written
  beside the code.
* **System check** names the commit Vercel built, so you can see which version is live.
