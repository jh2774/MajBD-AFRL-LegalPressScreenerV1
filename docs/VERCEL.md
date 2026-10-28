# Deploying FOCI-Screener on Vercel, with Supabase

The website runs on **Vercel** as one Python function, at **contractscreener.com**. Its
database is **Supabase** Postgres, connected to the Vercel project, and alert emails go out
through **Resend**. Anyone with the address can use the site without a key.

About 30 minutes. Everything is done in a browser: vercel.com, supabase.com (through
Vercel), resend.com and github.com.

## What you need

* A **Vercel** account (Hobby, free). Sign in with GitHub so Vercel can see the repository.
* Access to the GitHub repository `jh2774/MajBD-AFRL-LegalPressScreenerV1`, with the latest
  code pushed.
* The domain **contractscreener.com**, and access to wherever its DNS is managed (Vercel, if
  the domain was bought there or its nameservers point to Vercel; otherwise the registrar).
* Two values you make up now and keep somewhere safe:
  * an **admin key**: a long random string, say 40 letters and digits from a password
    manager's generator. Nobody needs it to use the site. It is for the few things kept for
    whoever looks after it (see "Who can do what");
  * a **contact line for the SEC**, which refuses requests without one:
    `FOCI-Screener (your.name@your-organisation.com)`.

## 1. Create the Vercel project

1. vercel.com → **Add New…** → **Project** → find `MajBD-AFRL-LegalPressScreenerV1` →
   **Import**.
2. Leave **Framework Preset** as Vercel detects it (FastAPI) and the root directory as `./`.
3. Open **Environment Variables** and add:

   | Name | Value |
   |---|---|
   | `FOCI_PUBLIC` | `true` — the site needs no key |
   | `FOCI_API_KEYS` | `default:` followed by your admin key — for example `default:k3J9…` |
   | `FOCI_USER_AGENT` | your SEC contact line |

4. **Deploy.** The build takes a minute or two.

**You should see** the build succeed. The site works at this point, but has no database yet:
every page shows a banner saying the database "will not survive a restart". That is
expected, and the next step fixes it.

If the build fails with *No FastAPI entrypoint found*, the code Vercel built is older than
this guide. Push the latest commit from GitHub Desktop; Vercel builds again on its own.

**Already have the project?** Add `FOCI_PUBLIC` = `true`, keep `FOCI_API_KEYS` as it is (its
key becomes the admin key), and redeploy: **Deployments** → the newest one → **⋯** →
**Redeploy**.

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

4. Redeploy so the site picks them up.

## 3. The address: contractscreener.com

Vercel → the project → **Settings** → **Domains**. `contractscreener.com` should be listed
with a **Valid Configuration** tick. If `www.contractscreener.com` is there too, set one to
redirect to the other; either way round works.

Nothing else to set: links in emails use the project's production domain, which Vercel tells
the site, so they point at `https://contractscreener.com`. If they ever point somewhere else
(a `.vercel.app` address, say), add `FOCI_PUBLIC_URL` = `https://contractscreener.com` and
redeploy.

## 4. Check it

Open **https://contractscreener.com**. No key is needed.

1. Click **Admin key** at the top right and paste your admin key — the part *after*
   `default:`. Only you need do this, on your own computer.
2. Open **System check** (the link at the bottom of every page). Every line should say
   **working** or **note**. In particular:
   * **Database** should read *Postgres database postgres at aws-…pooler.supabase.com, tables
     in schema foci. A row was written, read back and removed.*
   * the **SEC**, **IAPD**, **USAspending** and **FPDS** lines show Vercel can reach them, and
     that the SEC accepts your contact line.
   * **Screens** is a note about the time limit (below).
   * **Email alerts** says no mail service is connected yet. The next step connects one.

   A line that says **not working** says what failed and how to fix it.
3. The banner about the database is gone.
4. In a private browser window, which holds no key, the site opens and works the same, with
   **Admin key** at the top right where it used to say *API key*.

## 5. Email through Resend

Until a mail service is connected, every alert is written and shown on the Portfolio page
exactly as it would be sent, and nothing leaves.

### Verify the domain (about 10 minutes, then a wait)

1. Sign up at [resend.com](https://resend.com) (free: 100 emails a day, 3,000 a month).
2. **Domains** → **Add Domain** → `contractscreener.com`. Choose the region nearest the site
   (**North Virginia (us-east-1)**).
3. Resend lists DNS records to add: an **MX** and a **TXT** record named `send` (for bounces)
   and a **TXT** record named `resend._domainkey` (the signature). Then:
   * **If the domain's DNS is in Vercel:** press **Auto Configure**, sign in to Vercel when
     asked and allow it. Resend adds the records itself.
   * **Otherwise:** add the three records where the domain's DNS is managed, copying each
     value from Resend exactly. Where the form asks for a name, enter only `send` or
     `resend._domainkey`, without `.contractscreener.com` after it. Then **Verify DNS
     Records** in Resend.

   It usually verifies within minutes, and can take a few hours. The domain shows
   **Verified** when it is done.

### Give the site a key

1. Resend → **API Keys** → **Create API Key**. Name it `contractscreener`, permission
   **Sending access**, domain `contractscreener.com`. Copy it; it is shown once.
2. Vercel → **Settings** → **Environment Variables**, add:

   | Name | Value |
   |---|---|
   | `RESEND_API_KEY` | the key from Resend. Only ever here — never in the repository |
   | `ALERTS_FROM` | `alerts@contractscreener.com` |
   | `ALERTS_REPLY_TO` | optional: an address you read. Without it, replies go to `alerts@contractscreener.com`, where nobody reads them |

3. Redeploy.

### Check it

* **System check** → **Email alerts** reads *Sending is on … from alerts@contractscreener.com
  through Resend.*
* On the **Portfolio** page, start an alert list with your own address. An email arrives
  asking you to confirm (check spam the first time). Confirm it, then press **Send a test
  email**.

### Hear about bounces (recommended, five minutes)

Without this, an address that does not exist goes on being sent every alert, and the site
never knows. With it, Resend tells the site, which stops mailing that address and shows it
on the Portfolio page under **Not being emailed**.

1. Resend → **Webhooks** → **Add endpoint**. URL:
   `https://contractscreener.com/hooks/mail/resend`. Events: `email.bounced` and
   `email.complained`.
2. Copy the endpoint's **signing secret** (it starts `whsec_`). In Vercel → **Environment
   Variables**, add `RESEND_WEBHOOK_SECRET` = that secret, and redeploy.

`https://contractscreener.com/health` then shows `"bounce_reports": "resend"`. DEPLOY.md,
"Hearing about bounces", says what happens to a reported address.

**To read alerts yourself first**, also set `FOCI_EMAIL_REDIRECT_TO` to your own address:
every email, including the requests to confirm, then comes to you alone, with the intended
recipients named in the subject line. Remove it when you are happy with them. To stop
sending without disconnecting anything, set `ALERTS_SEND` to `false`.

## 6. The daily alert check

Email alerts are checked whenever someone opens the Portfolio page and the last check was most
of a day ago. For days nobody does, a GitHub workflow in the repository asks the site once a
day.

GitHub → the repository → **Settings** → **Secrets and variables** → **Actions** →
**Variables** tab → `FOCI_SITE_URL` → `https://contractscreener.com` (no slash at the end).
Create it if it is not there; change it if it points anywhere else.

The secret `FOCI_ALERTS_KEY` is no longer needed, since the site needs no key. Delete it, or
leave it set to the admin key.

To try it: **Actions** → **Daily alerts** → **Run workflow**.

The same setting also drives **Daily watchlists**, which re-screens each active watchlist on
the Watchlists page at 10:30 UTC, one at a time, so its notices are in that day's alerts.
Nothing on Vercel runs in the background, so without it a watchlist runs only when someone
presses **Run now**. Each screen stops after four minutes and keeps what it finished.

## 7. Sending notices

A notice is the email the screen drafts to the contracting officer on an award. It goes
nowhere until someone approves it on the **Notices** page. Approving then sends it to whoever
you pick: **the contracting officer**, **an address you choose** (your own, to read it first or
forward it yourself), **both**, or **nobody yet**. An approved notice has a **Send…** button for
later; one already sent has **Send a copy…**. A notice reaches the officer once at most.

On this site notices go through Resend, from `ALERTS_FROM`. (Gmail, the original route, needs
a sign-in saved on disk, which Vercel does not keep.)

* **Copies to an address you choose** work as soon as Resend is connected (step 5).
* **Sending to contracting officers** is switched off until you add `NOTICES_SEND` = `true`
  in Vercel and redeploy. There are no drafts here, so this is the deliberate step that
  lets an approval reach an officer.
* **Set `ALERTS_REPLY_TO`** to an address you read before sending one to an officer, or
  their reply goes to `alerts@contractscreener.com`, where nobody reads it.
* `FOCI_EMAIL_REDIRECT_TO` sends every notice to that one address instead, the intended
  officer named in the subject, as it does for alerts. A good way to try it first.

Every send is listed on the notice: where it went, when, and who sent it.

## 8. Signing in (recommended)

The admin key works, but it is one shared secret: pasted into each browser, impossible to take
back from one person without changing it for everyone, and recorded on every approval as
`api-key:default`, which names nobody. Signing in fixes that. Nobody else needs to sign in;
visitors use the site as before.

1. In Vercel, add `FOCI_ADMIN_EMAILS` = the addresses that may sign in, separated by commas,
   for example `julian@…, max@…`. Redeploy.
2. On the site, the button at the top right now says **Sign in**. Enter your address; a link
   arrives by email (it works once, for 20 minutes). Open it and press **Sign in**.

A signed-in browser can do everything the admin key can for 30 days, or until **Sign out**,
and what you approve and send is recorded under your address. Removing an address from
`FOCI_ADMIN_EMAILS` signs that person out everywhere at the next request. The admin key keeps
working alongside it, as a way in if email is down.

### Accounts for everyone else (optional)

To let colleagues keep their portfolios too, add `FOCI_SIGN_IN` in Vercel and redeploy:

* `FOCI_SIGN_IN` = `concordgroup.com` (one or more email domains, separated by commas) lets
  anyone with an address there sign in; or
* `FOCI_SIGN_IN` = `anyone` lets anyone with an email address sign in.

Signing in this way gives **no admin powers**. It gives an account:

* **Portfolios kept for you.** Build or open a portfolio while signed in and it is kept under
  your address. It opens on any browser where you sign in, and a change made on one shows on
  the others. **Your portfolios** on the Portfolio page lists them; a portfolio opened from a
  key has **Keep this portfolio in my account**.
* **Alert lists that follow.** An alert list started from a kept portfolio follows its edits
  from then on, wherever they are made: a company added on your laptop is watched without
  anyone re-saving the list. Your own address on a list is not asked to confirm; signing in
  already proved it is yours.
* **Email a portfolio**, to yourself or up to 10 colleagues at a time: the companies, the
  latest severity of each, a link that opens it on the site, and its key. To anyone else it
  says it came from you, and replies go to you. Up to 25 addresses a day per person.

The key still works as before, for anyone not signed in: it is the portfolio, and the account
is where your keys are kept. Sign-in links to anyone but the admins are limited to 30 a day
across the site, within Resend's free allowance.

## Who can do what

The site is open: anyone who has the address can see everything on it — every screen,
contractor, portfolio and alert list — and use it.

**Without a key**, anyone can search, screen agencies and contractors, build portfolios,
pick a contractor's SEC record, look up its funds, mark findings, create, run, pause and resume
watchlists and set their own policies, read notices, and start or add to an alert list.

**Signed in to an account** (section 8), as well: keep portfolios, open them on any browser,
and email them.

**Signed in as an admin, or with the admin key**, only:

* deleting a screen or an alert list;
* changing the screening rules or the site-wide screening policy;
* approving, rejecting and sending a notice;
* the **System check** page, which writes to the database and calls six outside services
  each time it is opened;
* seeing alert-list addresses, and where notices were sent, in full; taking an address off a
  list; "Mail again" for a bounced address.

**Email addresses are protected.** Visitors see the addresses on a list masked
(`j•••@agency.gov`). An address a visitor adds is sent one email asking it to confirm, and
nothing else until it does; an address you add while holding the admin key is trusted
without asking. Every alert ends with a link that unsubscribes that one address, which mail
programs also show as an **Unsubscribe** button, and the page it opens has an undo. Someone
who unsubscribes is not put back by anyone else, admin included: the Portfolio page names
who left, and a save that adds them again is declined by name. They rejoin from their own
link.

**Limits that keep a visitor from running up the email or the SEC's patience:** each
address is asked to confirm at most once a day, and the whole site sends at most 40 such
requests a day (Resend's free plan allows 100 emails a day in all). Without the admin key,
**Check now** waits 10 minutes after the last check, and **Send a test email** 10 minutes
after the last test to the same list.

To close the site again, remove `FOCI_PUBLIC` and redeploy: every page then needs a key from
`FOCI_API_KEYS`, entered with **API key** at the top right.

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

**Resend Free**

* **100 emails a day, 3,000 a month**, counting each recipient's copy and each request to
  confirm. An alert list of 10 people uses 10. If the day's allowance runs out part-way
  through an alert, the Portfolio page lists it as *sent to some* and names who it missed;
  an alert nobody received is tried again on the next check.

## Optional

* **Other keys** you may have — `SAM_API_KEY`, `USPTO_API_KEY` — go in Vercel's
  Environment Variables too.
* **Supabase's Data API.** This site does not use it, and keeps its tables in a schema the
  Data API does not publish (`foci`). To switch the Data API off altogether: in Supabase,
  open the project → **Integrations** → **Data API** → turn **Enable Data API** off.
* **Seeing the tables in Supabase.** Table Editor → change the schema at the top from
  `public` to `foci`.

## If something goes wrong

* **Logs:** Vercel → the project → **Logs**.
* **The database line in System check is not working:**
  * *password authentication failed*: the database password was changed in Supabase.
    Disconnect and reconnect the store in Vercel → **Storage**, then redeploy.
  * *Max client connections reached*: too many copies of the site are holding connections at
    once. It clears by itself within minutes.
* **Every page says the API is closed (503):** neither `FOCI_PUBLIC` nor `FOCI_API_KEYS` is
  set. Add `FOCI_PUBLIC` = `true` and redeploy.
* **"This needs an admin key, and this site has none":** `FOCI_API_KEYS` is not set. Add it
  (step 1) and redeploy.
* **Email:** the line under the Portfolio page's buttons, and **Recent alerts**, give Resend's
  own reason. A reason about the domain means step 5's verification is not finished, or
  `ALERTS_FROM` is not an address at `contractscreener.com`; one about the API key means
  `RESEND_API_KEY` was mistyped or the key was deleted in Resend.
* **A request to confirm never arrived:** check spam. A list's addresses still waiting are
  marked *asked to confirm* on the Portfolio page; saving the list again the next day asks
  any that were never sent one.

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
* **Links in emails** use `VERCEL_PROJECT_PRODUCTION_URL`, which Vercel sets to the
  project's production domain, unless `FOCI_PUBLIC_URL` says otherwise.
* **System check** names the commit Vercel built, so you can see which version is live.
