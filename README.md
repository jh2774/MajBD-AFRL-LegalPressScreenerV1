# foci-screen

Screens federal contract awards and the awardee's public disclosures for **changes** that
indicate Foreign Ownership, Control or Influence (FOCI), intellectual-property
collateralisation, or other IP risk — then drafts a notice to the contracting officer named on
the award.

The emphasis is on *changes*. A contractor that has always been Delaware-incorporated is not
interesting; one that filed an 8-K last week about a British Virgin Islands investor is. Every
document retrieved is hashed and stored, and rules run against the delta.

---

## Status

Working and tested end to end. 82 offline tests plus two live diagnostics.

| Step | State |
|---|---|
| 1. Contract discovery (agency → awards → contracting officer) | Working |
| 2. Contractor enrichment (SEC, IAPD, OFAC, USPTO, SAM, company web) | Working |
| 3. Email to the KO | **Renders only. Delivery deliberately inert** — see [Step 3](#step-3-email-deliberately-not-armed) |

Runs two ways from one codebase: a CLI over SQLite for a single analyst, or an HTTP API with a
job queue and Postgres for a deployment. See [Running as a service](#running-as-a-service).

---

## Install

```bash
pip install -e ".[browser]"
playwright install chromium
cp .env.example .env      # then set FOCI_USER_AGENT to a real contact address
```

The browser extra is optional but strongly recommended — without it investor-relations pages
cannot be read at all. Behind a TLS-inspecting corporate proxy, also `pip install truststore`;
the HTTP layer picks it up automatically and uses the OS trust store.

## Use

```bash
foci-screen agencies --filter navy

foci-screen screen --agency "Department of Defense" \
                   --sub-agency "Department of the Navy" \
                   --months 9 --awards 25 --entities 5

foci-screen screen --agency "Department of Energy" \
                   --domain "ACME DYNAMICS LLC=acmedynamics.com"

foci-screen notify --run <run_id> --preview
foci-screen history
```

Run the same command weekly against the same database. The first run is the baseline; from the
second on, findings are dominated by what moved.

---

## Data sources

Every endpoint below was probed directly while building this; the notes reflect what they
actually return, not what their docs claim.

| Source | Key needed | What it gives |
|---|---|---|
| **USAspending** `/search/spending_by_award`, `/awards/{id}` | no | Award population by agency, recipient UEI, parent entity, solicitation number, place of performance |
| **FPDS-NG ATOM** | no | **Contracting officer email**, `countryOfIncorporation`, `isForeignOwnedAndLocated`, `foreignFunding`, ultimate parent UEI |
| **SEC EDGAR** submissions + full-text search | no | 8-K item codes, SC 13D/G, and the exhibits where IP security agreements actually live |
| **IAPD** `api.adviserinfo.sec.gov` | no | Investment adviser registration and office country for investors behind a contractor |
| **OFAC SDN** CSV | no | Sanctions name screening |
| **SAM.gov** entity + opportunities | yes | Registered address, business types, solicitation point of contact |
| **USPTO** assignments | yes | Recorded `SECURITY INTEREST` conveyances against the patent estate |
| **Company IR / press / legal pages** | no | Announcements before they reach a filing |

Three things worth knowing, all observed rather than assumed:

- **The legacy keyless USPTO assignment API is gone.** Recorded IP liens are the single best
  evidence of collateralisation, and without `USPTO_API_KEY` the screen cannot check them. It
  prints `USPTO: skipped — IP liens NOT checked` rather than silently implying none exist.
  (`bulkdata.uspto.gov` and `search.patentsview.org` were also unreachable from this network —
  possibly proxy-blocked rather than down.)
- **Some investor-relations subdomains refuse automated clients, and are not read.**
  `investors.lockheedmartin.com` and `investors.leidos.com` sit behind Akamai bot management.
  They time out a plain HTTP client and return 403 to a browser that identifies itself. The
  tool does not disguise itself to get past that — see
  [Reading company websites](#reading-company-websites).
- **Web pages are fetched on a short leash** (12s, single attempt, at most 6 discovery probes).
  Government APIs are worth waiting for; an unresponsive corporate CDN must cost the run
  seconds, not minutes.

### Reading company websites

Government APIs are built for programmatic access. A contractor's newsroom is not: it is a
website this tool crawls on a schedule without being asked. For a tool that supports compliance
work, three rules follow.

**It obeys `robots.txt`**, on every fetch path, following RFC 9309. If the file is missing (4xx),
nothing is restricted. If the server doesn't answer (5xx or no response), everything is treated
as disallowed for that run — the RFC rules out assuming the answer would have been yes.
`Crawl-delay` is honoured up to 10 seconds; a host asking for more is skipped rather than
crawled faster than it asked.

**It says what it is.** Plain requests carry `FOCI_USER_AGENT`, which must include a contact
address. The headless browser keeps Chromium's engine tokens, since some JavaScript apps pick
their code path from them, and appends the same identity.

**It does not work around a refusal.** Fetching is two-tier: a plain GET first, then headless
Chromium if that yields a timeout, an error, or an empty application shell. A page the browser
gets as an HTTP error is discarded, not stored.

> **Correction.** v0.3–v0.6 did disguise the browser: a bare desktop-Chrome user agent, plus a
> launch flag whose only purpose is hiding automation from bot detection. That is how those
> versions read `investors.lockheedmartin.com` (905 characters) and `investors.leidos.com`
> (1,952). Identified honestly, both hosts return **403 from Akamai** and their `robots.txt`
> never answers, so they are not read. The earlier figures in this README reflected the disguise
> and have been withdrawn.

Measured live after the change, for the Lockheed screen:

| Host | Result |
|---|---|
| `lockheedmartin.com` (5 pages: home, newsroom, history, governance) | Read over plain HTTP; `robots.txt` allows |
| `investors.lockheedmartin.com` | Not read; `robots.txt` unreachable, browser refused (403) |
| `investors.boeing.com` | Allowed, with `Crawl-delay: 10`, which is honoured |
| GD, RTX, Northrop, HII newsrooms | Allowed |

Re-check with `python tools/check_web_coverage.py [urls...]` after touching the fetch logic. It
goes only through the compliant connector, and it counts a refused host as a reported outcome,
not a failure. Its predecessor, `check_ir_pages.py`, bypassed `robots.txt` and scored a refusal
as a regression to fix; it has been removed.

What goes unread is written into the run notes, with where else to look. Material announcements
also appear as SEC 8-K filings, which this screen already reads. The IR sites that refuse are
mostly a *faster* copy of what EDGAR carries, not a separate source — though an announcement can
reach the IR site hours or days before the filing.

Finding the IR problem originally also surfaced a larger bug that had nothing to do with browsers. Page
normalisation preferred the `<main>` element, and Lockheed's newsroom puts a *navigation rail*
in `<main>` with the actual press releases outside it — so 150KB of HTML normalised to 97
characters of menu labels, and every rule downstream was reading a nav menu. `<main>` is now
used only when it carries enough text to plausibly be the content. That single fix took the
page from 97 to 3,720 usable characters, and it affected every site the tool watches, not just
the ones that needed a browser.

Without the browser extra installed nothing crashes. Unreadable hosts — no browser, refused,
or disallowed — are reported in the run notes, because silence there would read as "nothing
found on their website", which is a different claim entirely.

### Subcontractors

`--subcontractors N` screens N suppliers underneath the primes. This is where FOCI risk
concentrates — small, cash-hungry firms — and it is exactly what a ranking by obligated dollars
buries. A live Navy run surfaced `INCHCAPE SHIPPING SERVICES(JAPAN) LTD.` as a supplier under a
Navy prime; nothing in the prime population would have shown it.

Subaward data is weaker than prime-award data, in ways established by querying the endpoint:

- **The sub-agency filter is ignored.** Asking for Defense + Navy returns byte-identical results
  to asking for Defense alone: 42 Navy, 26 Air Force, 19 Army out of 100. The filter is
  therefore applied client-side, or a screen scoped to one command quietly reports another
  command's suppliers as its own.
- **Values are self-reported and frequently wrong.** The largest "subaward" in a live sample was
  $5.0B to a machine shop, and the median was $53M. They are never used for ranking — ordering
  is by date — and a notice labels the figure as self-reported rather than quoting it as an
  obligation.
- **Some subawardees are people.** Sole proprietors appear in the data; `JOSHUA D GOODWIN` came
  back under a Navy prime. Screening a named individual, then writing to their customer's
  contracting officer about them, is a different act from screening a company, and the tool
  declines by default. The test is a heuristic — a two-word company with no "Inc" can land in
  it — so skipped names are listed in the run notes instead of disappearing.

A subcontractor has no contracting officer of its own, so a notice about one goes to the KO on
the **prime** contract, and says so in its first sentence: the Government has no privity with
the subcontractor, the prime does. FPDS data about the prime's vendor is never copied onto the
subcontractor.

### How the contracting officer is resolved

FPDS records who created, approved and last modified each contract action, and for DoD those
are real addresses (`JANE.DOE.N00019@JSF.MIL`). Preference order:

1. `approvedBy` — the warranted official → confidence **high**
2. `lastModifiedBy` — may be a specialist or administrator → **medium**
3. `createdBy` → **low**
4. SAM.gov solicitation point of contact, when a key is configured

The confidence and its basis are printed in the notice footer so a recipient who is not the
cognisant KO can redirect it.

---

## Calibration

Two failure modes matter, and they pull in opposite directions.

**False positives are the expensive ones.** A notice that tells a contracting officer a healthy
prime is collateralising its patents is worse than no notice — it burns the credibility of the
whole feed. Four mechanisms hold precision:

- *Attribution.* EDGAR full-text search is constrained by CIK. Querying
  `"patent security agreement" "Lockheed Martin"` as free text returns **other registrants'**
  exhibits, which the tool would then attribute to Lockheed. Without a confident CIK it returns
  nothing rather than guessing.
- *Whole-term matching and proximity.* Terms match on word boundaries, and a jurisdiction only
  escalates when ownership language appears within ~800 characters of it. Both were learned
  from a real false positive: `SPAC` matched inside the word **"space"**, which on an aerospace
  contractor's history page combined with a missile range in the Marshall Islands to produce a
  CRITICAL foreign-investment notice about nothing at all. A jurisdiction named without a
  nearby transaction is reported as `FOCI-MENTION-01` and explicitly labelled as possibly
  geographic or historical.
- *Corroboration before compounding.* The compound rule requires both halves to reach `medium`
  independently, and a bare mention is ineligible. Two weak observations do not make a strong
  one.
- *No self-matching.* A `Document`'s text is always source text, never a summary the tool
  composed. A summary naming the phrase we searched for will match the rule that looks for that
  phrase, and the tool will cite its own query as evidence. 8-K item meanings are carried as
  codes in metadata and interpreted by a structured rule for the same reason.
- *Evidence quality.* A phrase in an executed 8-K exhibit and the same phrase in a 10-K risk
  factor are not equivalent. Matches are damped for periodic reports (×0.35), conditional
  phrasing (×0.5), and definitional or event-of-default clauses (×0.4) — every
  investment-grade revolver defines default to include the borrower's bankruptcy, and that says
  nothing about the borrower.
- *Corroboration cap.* Composite severity may exceed the worst individual signal by at most one
  band, and only when two signals independently reach it. A pile of weak hits cannot manufacture
  a CRITICAL.

**False negatives are the reason the tool exists.** `tools/positive_control.py` runs the live
connectors against registrants known to have filed an Intellectual Property Security Agreement
and fails loudly if the rules do not fire. Run it after touching lexicons or weights:

```bash
python tools/positive_control.py
```

Observed separation on real data: a registrant with First and Second Lien IP Security
Agreements scores **HIGH (20.9)**; Lockheed Martin over the same window — including its own
newsroom and governance pages — scores **MEDIUM (17.2)**, with its top signal correctly
labelled as a mention with no transaction identified and no compound risk raised.

### Compound rule

The case the tool exists to catch is the overlap: a foreign or secrecy-jurisdiction nexus
appearing on a contractor whose patents are already encumbered. That combination describes a
path by which technology developed under contract could come under foreign control without a
novation or FOCI action ever reaching the KO. It is scored as its own signal (`COMPOUND-01`).

---

## Step 3: email, deliberately not armed

Per the build request, delivery is wired but off. Three independent guards:

| Guard | Default | Effect |
|---|---|---|
| `GMAIL_ENABLED` | `false` | Notices render to `./out/*.eml`; nothing touches the network |
| `GMAIL_SEND` | `false` | With Gmail enabled, creates **drafts** rather than sending |
| `FOCI_EMAIL_REDIRECT_TO` | unset | While set, every notice goes to this address instead of the KO |

To arm drafting: enable the Gmail API in a Google Cloud project, create a Desktop OAuth client,
download `credentials.json`, `pip install -e ".[gmail]"`, then set `GMAIL_ENABLED=true` and
`GMAIL_SENDER`. Scope is `gmail.compose` — draft creation only. `gmail.send` is requested only
if `GMAIL_SEND` is true.

**Recommendation: leave `GMAIL_SEND=false` permanently and keep a human in the loop.** These
notices go to federal officials about named companies. A false positive mailed automatically is
a real harm to a real contractor, and the person best placed to catch one is whoever reads the
draft. Set `FOCI_EMAIL_REDIRECT_TO` to your own address for the entirety of any pilot.

An unresolved KO is never guessed at: the notice is written to disk and logged as `suppressed`.

---

## Running as a service

The screening core takes its dependencies by injection and returns plain dataclasses, so the
same `Screener.run()` that backs the CLI backs a queue worker unchanged. What the service adds
is everything around it.

```bash
pip install -e ".[server]"
export DATABASE_URL=postgresql://...      # SQLite is single-process only
export REDIS_URL=redis://...
export FOCI_API_KEYS="acme:$(openssl rand -hex 24)"

uvicorn foci_screen.api.app:app --port 8000   # API
python -m foci_screen.worker                   # worker (carries Chromium)
python -m foci_screen.scheduler                # nightly sweep, from cron
```

A screen takes minutes, so `POST /v1/screens` creates a run, queues it, and returns a `run_id`
to poll. Without `REDIS_URL` jobs run in a background thread instead — fine for a laptop, wrong
for a deployment. In that mode the scheduler still prunes old revisions, then refuses to
enqueue work that would die with the process, and **exits with status 2** so the cron run is
marked failed. An earlier version exited 0 there, which would have shown a green scheduler
every night on a deploy that was screening nothing.

| Route | Purpose |
|---|---|
| `POST /v1/screens` | Start a screen; returns `run_id` (202) |
| `GET /v1/screens/{id}` | Status, progress, findings when complete |
| `GET /v1/search?q=&kind=` | One query over contractors, awards, officers, agencies |
| `GET /v1/overview` | Everything the dashboard charts, in one call |
| `GET /v1/findings` | The change feed, filterable by severity and date |
| `GET /v1/entities/{key}` | Profile, contracts, signal history |
| `GET /v1/officers/{email}` | What one contracting officer holds |
| `GET /v1/agencies/{name}` | Contractors and officers under one agency |
| `GET /v1/contracts/{piid}` | One award, with its data-rights clauses |
| `GET /v1/documents?entity_key=` | Source documents read while screening a contractor |
| `GET /v1/documents/diff?source=&key=` | **What changed between two revisions** |
| `DELETE /v1/screens/{id}` | Remove a screen and its data — reports first, deletes on `confirm=true` |
| `GET /v1/suggest?q=` | Type-ahead: screened matches, plus contractors not screened here |
| `GET / PUT / DELETE /v1/policy` | What this tenant screens for and what raises a notice |
| `GET /v1/advisers/search?q=` | Investment firms by name, from the SEC's adviser database |
| `GET /v1/advisers/{crd}` | One firm's Form ADV funds, in plain terms, and changes seen |
| `POST /v1/alerts/subscriptions` | Save who is emailed about a portfolio |
| `POST /v1/alerts/run` | Check every watched firm now and email what is new |
| `POST /v1/policy/preview` | What a proposed policy would do over past findings — saves nothing |
| `POST /v1/portfolio/key` | Mint a portfolio key from a list of companies |
| `POST /v1/portfolio/edit` | Add or drop companies, returning a new key |
| `POST /v1/portfolio` | Open a key into a dashboard of those companies |
| `POST /v1/watchlists` | Agencies to re-screen on a schedule |
| `GET /v1/notices` | The review queue |
| `POST /v1/notices/{id}/approve` | The human gate. Records a decision — does not send |
| `GET /v1/notices/{id}.eml` | Download the notice to route by hand |

**Auth fails closed.** Keys are `tenant:key` pairs in `FOCI_API_KEYS`. With that unset, every
authenticated route returns 503. A tool that drafts email to federal officials must not be
reachable because someone deployed it before setting a variable.

**Tenancy.** Runs, findings, notices and watchlists are tenant-scoped. Snapshots deliberately
are not — a document's hash is a fact about the world, not about who is watching, so ten
tenants screening the same prime fetch its 10-K once between them.

> The CLI writes as tenant `default`. A key that should see CLI-produced data must map to that
> tenant (`FOCI_API_KEYS=default:...`), or the web UI will correctly show you an empty database.

## The web application

Served by the same process at `/`. Search anything the screen has seen — a company, an award,
a contracting officer, an agency — from one box, and follow it through:

- **Overview.** Obligated total, contractors by latest severity, signals by category, findings
  per day, largest contractors coloured by severity.
- **Contractor.** Signals with the matched evidence, registration, severity over time, awards,
  and every source document the screen read.
- **Document changes.** A unified diff between two revisions of a filing or a web page. This is
  what the change-detection design exists for: not *this company mentions the Cayman Islands*
  but *these three lines appeared on their newsroom last Tuesday*. Additions and deletions carry
  a `+`/`−` sign as well as a colour, so the diff is readable without relying on hue.
  **Lines a rule actually fired on are marked with that rule's id**, so the finding and the
  change are joined up rather than left side by side for a reviewer to pair by eye. When none
  of the rules match a changed line, the page says so — that means they fired on text which was
  already there, which is a weaker basis than a fresh disclosure and should read as one.
- **Contracting officer.** Everything one KO holds and which of their contractors are flagged —
  the view that answers *who would this notice go to, and what else are they responsible for?*
- **Agency.** Contractors by obligated value, and the officers underneath.
- **Award.** Full record, data-rights clauses, resolved KO.
- **Notices.** The review queue. Approve, reject with a reason, or **edit the wording and then
  approve**; download the `.eml`. Three rules hold the gate:
  - *The generated text is kept.* An edited notice stores the original alongside what was
    approved, and the card shows what the reviewer changed as a diff. What the tool wrote and
    what a person sent are different claims, and that difference is the first thing asked about
    when a notice is challenged.
  - *The limitations statement cannot be edited out.* Anything else can be reworded. Without
    that paragraph a notice reads as a finding against a named company rather than a screen, so
    a body missing it is refused (422) — and the refusal appears next to the editor, so a long
    edit is not lost to one validation message.
  - *Decisions are final.* A rejected notice cannot later be approved (409). A rejection is the
    only labelled false-positive data the tool gets, and it must not be quietly overturned.

  With no reviewer name in the request, decisions are attributed to the key as `api-key:<tenant>`.
  Keys are shared per team, so this identifies the team, not the person — individual
  attribution needs SSO.

No framework, no build step, no CDN: it ships inside the API image as three static files, which
matters on a network that blocks outside origins. Charts are hand-rolled — horizontal bars are
HTML, only the sparkline is SVG.

An entity with awards but no finding is labelled **"no findings"**, not "not screened". It was
screened and produced nothing at or above the run's threshold, and in a risk tool those two
claims are opposites. A first observation is labelled a **baseline** for the same reason — all
of it reads as new, and that is not a change the contractor made.

### Revision retention

Diffs need prior text, so revisions are stored content-addressed by hash: a body is kept once
however many documents or tenants arrive at it. The nightly scheduler prunes to the 20 most
recent revisions per document and collects orphaned bodies; hashes are kept regardless, so a
revision that existed is still *known* to have existed after its text is gone, and the UI says
"text no longer retained" instead of showing an empty diff.

Upgrading an existing database backfills bodies from the current snapshots, so the next change
to each document is diffable rather than each needing to change twice first.

### Press, financial and legal releases

Scraping a newsroom is the worse route even where it works. The page carries navigation, a
cookie banner and a "related items" rail, all of which change for reasons that have nothing to
do with the company — and several investor-relations hosts refuse an honestly identified
crawler outright.

A release feed is the better shape *and* the permitted one. Q4, Notified and EQS all publish
RSS or Atom for this purpose, so `connectors/feeds.py` looks for one, and turns each entry into
its own document keyed on the release URL. That matters for change detection: a new
announcement arrives as a **new document** rather than as a diff against a page that has been
rearranged, and the engine already scores evidence that appeared since the last screen above
evidence that has sat there for years.

Releases are sorted into **press**, **financial** and **legal** from the headline and
standfirst, matched on whole words. Where an announcement is both — "Q3 results; company
settles litigation over pricing" — the legal reading wins, because that is the reportable half.

Every request goes through `WebWatchConnector`, so robots.txt, crawl delay and the honest
User-Agent are enforced in one place for feeds and pages alike. There is no second HTTP path.

Discovery tries the company's own domain first, then the hosts a newsroom usually sits on —
`news.`, `investors.`, `media.`, `investor.`, `ir.` — because that is where an off-the-shelf
investor-relations site serves its feed. On those hosts the platform paths are tried first: Q4
and Notified answer on `/rss/news-releases.xml` and `/rss/pressrelease.aspx`, and with a budget
of six probes, leaving those at the end of a general list meant they were never reached on the
host most likely to have one.

**Coverage is patchy, and this is measured rather than assumed.** Of six primes checked live,
two publish a usable feed: General Dynamics on its own domain, and Huntington Ingalls at
`ir.hii.com/rss/pressrelease.aspx` — found only once subdomains were tried. Lockheed, RTX,
Northrop and Leidos advertise nothing the probe budget finds; their investor-relations hosts
either do not resolve under the usual names or stall behind bot management, and the page crawl
remains their only route.

A guessed subdomain that does not exist costs one cached DNS failure and is then skipped. It is
not reported as reduced coverage either: a name nobody claimed existed is not a site refusing
us, and listing five of them per company would bury the refusals that do mean something.

**One trap, found live.** A content management system will serve `/rss.xml` listing every page
on the site — "Homepage", "Insights", "Climate Solutions" — with real publication dates
attached. Accepted, it would have filled a contractor's document history with furniture and
diffed it forever after. Dates do not separate that from a release feed; both have them. Title
length does, since a headline is a sentence and a page title is a label, so a feed whose median
title runs under four words is ignored. It is a heuristic, biased towards rejecting: missing a
feed costs coverage the page crawl may still get, while accepting the wrong one puts navigation
in front of a reviewer as though it were evidence.

### Alerts that arrive by email

The four primes with no readable feed will mail you the same releases. Every investor-relations
platform offers alerts to anyone who subscribes, and that is the only route that reaches a host
serving its pages to browsers alone.

Point `FOCI_ALERTS_DIR` at a directory of saved `.eml` files — what a mail client writes when
you export a message, and what a sync tool drops on disk — and those releases join the screen:

```bash
export FOCI_ALERTS_DIR=~/ir-alerts
```

**No credentials, deliberately.** Connecting to the mailbox itself means holding a password or
an OAuth token for the account a person's notices arrive in, which is a much larger thing to
get right than it looks. A folder is enough to be useful and cannot leak anything.

A release that arrives by both feed and email is **one document**, because the key is the
release URL either way — two sightings of one announcement rather than two that each read as
new. The connector picks the link that points at the company, so the tracking redirect, the
"view online" and the unsubscribe are not mistaken for the release; and a folder holding
ordinary mail is not a problem, since a message that neither names the company nor links to it
is left alone.

## The award record changing

Documents are not the only thing that moves. An award's own record can change hands, and when
it does the contracts row is written over — so nothing afterwards says it happened. Three
fields are watched for what they mean rather than for tidiness:

| What moved | Rule | Why it matters |
|---|---|---|
| The award now sits against a different contractor or UEI | `CHANGE-NOVATION-01` | A **novation**. The agreement behind it is where a change of ownership would be documented |
| Country of incorporation or registered address | `CHANGE-COUNTRY-01` | The most direct structural indicator in the contract record. Weighted by destination, so a move to a covered nation outscores a move to an ally |
| FPDS foreign-owned-and-located newly reads true | `CHANGE-FOREIGN-OWNED-01` | The contractor's own certification changing is a statement that something about the ownership did |

The transitions are kept in `contract_changes` and shown on the contractor page under
**Changes to the award record**, which is the only place the previous value survives.

Three things deliberately do *not* fire. A first sighting is a baseline, not a change. A field
the source stopped returning is missing data, not a contractor acting — and it no longer
overwrites the stored value either, since a connector outage should not be able to erase a
country of incorporation. A withdrawn certification, or a contracting officer being reassigned,
is recorded in the log without becoming a finding.

## Screening a company you already have in mind

Search covers what has been screened, so a firm this database has never looked at matches
nothing — correctly, but it used to be a dead end. `agency` was required, so the only way into
the database was to screen a whole department and take whoever turned up in it.

A screen can now name a contractor instead:

```bash
foci-screen screen --recipient "RAYTHEON COMPANY" --months 12 --entities 3
```

```bash
curl -X POST $API/v1/screens -H "Authorization: Bearer $FOCI_KEY" \
  -H "Content-Type: application/json" \
  -d '{"recipient":"RAYTHEON COMPANY","months_back":12,"max_entities":3}'
```

Naming both narrows to one contractor's work for one department. USAspending matches the text
against recipient name *and* UEI, so a pasted UEI works as well as a typed name.

In the web interface this is where a fruitless search leads: searching for a contractor that
has not been screened offers to screen it, and the run page then shows progress while it works.

**Removing a screen.** One run against the wrong agency puts contractors on the dashboard that
nobody chose to watch. `DELETE /v1/screens/{id}` reports what would go and deletes nothing;
repeating it with `confirm=true` removes the run, its awards, findings and pending notices.
Stored document revisions are shared between tenants — a hash is a fact about the world, not
about who was watching — so they are never touched.

## Watching investment firms: Form ADV

The firms that own or back a defence contractor — private equity, venture capital — file
**Form ADV** with the SEC and must keep it current. Contractors themselves do not file it; their
investors do. Its **Schedule D, Section 7.B.(1)** lists each private fund the firm runs: how
much money is in it, how many investors, where it is set up, and — question 16 — **how much of
it is owned by investors outside the United States**.

Add a firm on the **Portfolio** page (search by name; it is added by its SEC CRD number) and its
page shows those funds in plain English. When it files again, the tool reports:

* a **new private fund** — the firm raising money; funds are partnerships, so this is also the
  "new partnership" case
* the **share owned outside the U.S.** rising or falling in any fund — ordered first
* a fund's **investor count** changing, or its **size** moving by a quarter or more
* a fund **disappearing**, a fund's **home country** changing, a new **related firm**, and
  every **new filing**

Each report says what changed, one sentence on what that part of the form *is*, and exactly
where on the form to look, with links to the SEC's page and the filing. It does not say what
the change means for the contractor; that is left to the reader.

How it reads the form, and why this way. IAPD's JSON record gives each firm's latest filing
date, so most nights nothing more is fetched. When the date moves, the full Form ADV is
downloaded as a PDF: `adviserinfo.sec.gov` serves the same form section by section as web
pages, but its robots.txt disallows those to automated clients, and this tool does not read
them. The PDF is built so each page repeats everything before it in its section — reading all
63 pages of a real filing took 27 seconds. The tool finds the pages that end each section from
their drawing sizes, which cost nothing to read, and extracts only those: 2.5 seconds, with
identical results, checked field by field against reading every page. It uses the fast path
only when it recovers every fund the form declares, and reads every page otherwise.

## Email alerts on a portfolio

A portfolio can carry a list of email addresses. Whenever something changes for one of its
companies — a Form ADV change at a firm, or a contractor being flagged under the screening
policy — the list gets one email with every new item: what changed, what it is, and where to
look. **No analysis**; the reader decides.

* Everything already known when a list is saved is treated as sent, so the first email is about
  something that happens *next*, not the firm's history presented as news.
* Each item goes to each list once. A failed send is retried next time.
* **Sending is off until `ALERTS_SEND=true`.** Until then, every alert is written and shown on
  the Portfolio page exactly as it would be sent. `FOCI_EMAIL_REDIRECT_TO` still applies, so a
  pilot can route every alert to one person first.
* On the free plan, a GitHub Actions workflow triggers the daily check. DEPLOY.md has the setup.

## Choosing what to screen for, and what raises a notice

The **Screening** page holds a policy per tenant, in two halves.

**What to screen for.** Risk categories — FOCI, corporate structure, IP pledged as collateral,
IP transfer or distress, sanctions, company disclosures — and which kinds of company release to
read at all. A category switched off is dropped before anything is scored, so it can neither
raise a finding nor combine with another into one; a release kind switched off is not read, so
it builds no history either. Individual rules and their weights stay on the **Rules** page,
which is the finer layer underneath.

**What raises a notice.** A severity threshold (low to critical — not "info", since a notice
about something scored as informational has nothing in it to act on); which categories may
raise one; whether a contractor's first screen notifies or is a silent baseline; and events to
**always** be told about regardless of the threshold — a novation, a change of registered
country, a new foreign-ownership certification, a sanctions match, a new legal or financial
disclosure.

**Each piece of evidence notifies once.** This is the part that matters most, because until
now a notice had no memory: every finding at medium or above queued a draft on every run, so a
contractor on a nightly watchlist with a standing finding sent the same draft to the same
contracting officer every night. Now each notice records the evidence it covered, and evidence
already covered does not raise another. A **rejected** notice counts as covered — a reviewer's
"no" is the only labelled false positive this tool gets, and the next screen must not overrule
it. When genuinely new evidence arrives for a contractor that already has an undecided draft,
the new draft **supersedes** the old one, so the queue holds one current notice per contractor.

Notices written before this existed recorded no evidence, so the findings they were raised from
stand in for them. Without that, the first screen after upgrading would re-notify everything
ever notified — checked against a real database, where the preview reported all three existing
notices as already covered.

Every notice now says **why it was raised**, and a screen reports why the others were held
back, so a quiet queue can be told apart from a misconfigured one. The page's **Preview** runs
a proposed policy over each contractor's latest finding before anything is saved; it previews
the notice half only, because what a different screening selection would have found is a
question about evidence never gathered.

New legal and financial releases become **disclosure** signals scored at zero. That something
was announced is not evidence of risk — other rules score what it says — and a quarterly
results release adding weight every quarter would inflate severity on a calendar. They exist so
"always notify me of a new legal disclosure" has something to fire on, and only a release new
since the last screen produces one: on a company's first screen, its feed is backlog, not news.

## The dashboard is a portfolio

The overview opens empty. It used to open on everything in the database, which is whoever the
last departmental screen happened to surface — a dashboard nobody chose, presented as though
they had. Now it shows the companies you picked, and until you pick some it says so and gets
out of the way.

Picking is always a button. Searching for a company does not start watching it, and neither
does reading its page: **Add to portfolio** sits on the contractor, officer and agency pages,
and on an officer or agency it adds the contractors listed there. The first add starts a
portfolio, so nothing has to be set up first.

## Type-ahead

The search box suggests as you type, from two places that mean different things:

* **Screened here** — contractors, officers and agencies this database can answer about now.
  A SQL `LIKE` over the local tables; instant.
* **Not screened here yet** — contractors that exist in federal contracting but have never
  been screened on this deployment, from USAspending's recipient autocomplete. Choosing one
  offers to screen it.

**Typing never screens anything.** A screen takes minutes and walks half a dozen government
APIs; one per keystroke would be both absurd and a rude way to treat a public service.
Suggesting is reading. Screening stays an explicit action behind a button.

The remote half is best effort, and deliberately so. Measured against the live endpoint, a
long prefix like `huntington ing` answers in about a second while short ones such as `rayth`
have been seen to time out at twenty — the cost is in how many recipients the prefix matches,
so the short prefixes a type-ahead sends first are the expensive ones. It is therefore
debounced, floored at four characters, capped at a four-second timeout, cached per prefix, and
allowed to come back with nothing. The box never waits on it, and local suggestions appear
regardless.

## Portfolio keys

A portfolio is a named list of companies you watch, and the key is **that list encoded into one
line of text** — not a pointer to a row in a database:

```
FOCI-PORTFOLIO-1.eNpljk1rhDAURf9KyKKbOiV-zphd1GeMMYkkURllVoVCoVQoQzel_70Zt10-zrn33R_8ium2Y…c33f6f44
```

Save it in a file. Paste it into **Portfolio** and the dashboard opens: obligated value across
the portfolio, a severity breakdown, and every company with its latest finding.

Carrying the list inside the key rather than storing it server-side is the whole point, and it
follows from how this gets deployed. On a free instance whose database has already been
discarded once, a saved dashboard that lives on the server is a saved dashboard that
disappears. This one opens on another browser, on another machine, against a different
deployment, and against a database rebuilt from nothing — because the only thing it needs is
the text you kept.

Two consequences worth stating plainly:

* **A key is not a credential.** It holds company names and nothing else, and anyone you send
  it to can open the same list. You still need an API key to reach the deployment at all.
* **A truncated key is refused, not partly loaded.** Keys carry a checksum because the
  realistic failure is a line cut short on its way out of an email. A portfolio that quietly
  loaded eight of its ten companies would leave two companies unwatched by someone who believed
  they were watching them.

A company in the key that this deployment has never screened is listed as *not screened here*
rather than dropped. That is the useful half of the answer — it is the list of what to screen
next.

## Who this contractor is

Name matching between USAspending, SEC, IAPD and USPTO is the weakest link in the chain, and
the CIK is the one that bites: EDGAR full-text search is *constrained* by CIK, so a wrong
mapping does not come back empty — it comes back with another registrant's exhibits attached to
this contractor's name. That is the first bug this project ever had.

Resolutions are now stored rather than recomputed, because a similarity score rerun each week
against a changing ticker file can land somewhere new without anyone noticing:

| | |
|---|---|
| `GET /v1/identity?status=auto` | The review queue, least confident first |
| `GET /v1/entities/{key}/identity` | What this contractor was resolved to, and how confidently |
| `POST /v1/entities/{key}/identity` | Confirm, correct, or reject it |

A human verdict outranks the matcher **permanently, in both directions**:

- **Confirmed** is never re-resolved. The matcher does not get a second opinion after a person
  has decided, and no lookup is even attempted.
- **Rejected** means no registrant has been identified — not merely "unreviewed". Re-deciding it
  by similarity on the next run would reintroduce exactly the misattribution the reviewer had
  just removed, so the screen stops attributing filings to that contractor until someone says
  otherwise.

Unreviewed mappings do keep tracking the matcher, so improving the matcher still helps
everything nobody has looked at yet. The entity page shows the CIK, the matched registrant
title, the match percentage, and who decided.

## Measuring the rules

Every weight in `risk/engine.py` — `IP_COLLATERAL` at 7.0, the China multiplier at 3.0, the
×0.35 damping for periodic reports — was set by judgement. None of it has ever been checked
against an outcome.

The web UI puts three buttons under each signal, where the reviewer is already reading the
evidence: **confirmed**, **false positive**, **unclear**. Each verdict is one labelled example,
and `#/rules` is what they add up to:

| | |
|---|---|
| `POST /v1/dispositions` | Record a verdict on one signal |
| `GET /v1/rules/precision` | Per-rule precision, worst first |
| `GET /v1/entities/{key}/dispositions` | Verdicts recorded against one contractor |

Three decisions in how this counts, each of which could have gone the flattering way:

- **"Unclear" is kept out of the denominator.** A reviewer who could not tell has not said the
  rule was wrong, and folding that in would punish rules that raise genuinely hard questions.
- **A rule with no verdicts reads "not measured", never 100%.** Unmeasured is not perfect.
- **Verdicts are revisable.** A notice decision is final because it is an action; a verdict is
  a judgement, and a reviewer who looks again and changes their mind is producing better data,
  not a second data point.

A signal is identified by a hash of its rule and its evidence, not by the rendered wording, so
rewording a rule's rationale does not orphan the verdicts already recorded against it. Findings
written before this existed get an id computed on read, so old findings can still be marked.

### Acting on it

Seeing that a rule does not earn its place is only useful if you can do something about it
without a deploy. The same page carries a weight and an on/off switch per rule:

| | |
|---|---|
| `GET /v1/rules` | Every rule seen here, with its precision and its settings |
| `PUT /v1/rules/{rule_id}` | Disable it, or scale its weight (0–5) |
| `DELETE /v1/rules/{rule_id}` | Drop the override, back to the engine default |

- **Weight rescales the score and the severity band is recomputed from the result.** A rule
  scored down to 2.0 does not keep a "critical" label it no longer earns.
- **A disabled rule is removed before anything compounds**, so a rule you retired cannot
  escalate a contractor by pairing with another through the compound rule.
- **Reset deletes the override rather than writing 1.0**, so a later change to the engine
  default takes effect for you.
- Settings are per tenant and apply to the **next** screen, not to findings already recorded.

This is also the input the remaining ranking problem needs: ordering contractors by risk
rather than by obligated dollars.

## Session log

The conversation this was built from — the prompts and the replies, in order. Most of the
reasoning behind the awkward parts (why EDGAR search is CIK-constrained, why `<main>` is not
trusted, why delivery stays inert) happened there rather than in commit messages. Generate it
with:

```bash
python tools/export_session_log.py        # writes docs/SESSION_LOG.md
```

**It is deliberately not committed**, and `.gitignore` keeps it out. The export redacts
secrets, personal email addresses and home paths, but it keeps government contact addresses,
and it quotes draft notices naming real contractors. In a public repository that combination is
a liability rather than a record. Generate it locally, read it, and decide per destination.

Every tool in `tools/` is Python, so the repository runs the same way on Windows, macOS and
Linux. The only non-Python code is the web interface's HTML, CSS and JavaScript, which a
browser requires.

### Deploying to Render

`render.yaml` is a Blueprint: in Render, **New → Blueprint** against the repository. It defines
Postgres, Redis, the API, the worker, and a nightly cron job. Two images, because only the
worker needs Chromium and its ~700MB of system libraries; the API is redeployed far more often
and has no use for any of it.

Render prompts for the values marked `sync: false` on first deploy — at minimum
`FOCI_API_KEYS` and `FOCI_USER_AGENT`. Set `FOCI_EMAIL_REDIRECT_TO` to your own address for the
whole of any pilot.

Two things to know before you commit to it:

- **The worker needs the `standard` plan.** Chromium will OOM on `starter`. This is the main
  running cost, and it is why the browser lives in its own service.
- **Render's free Postgres expires after 30 days.** The blueprint asks for `basic-256mb`.
  Losing the database loses the baseline, and without a baseline every document reads as new
  on the next run — which is exactly the alert storm the change-detection design exists to
  prevent.

Schema migration runs automatically when the store opens, including on an existing v0.1 SQLite
file — `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so missing
columns are added explicitly.

---

## Limitations

- **This is a screen, not an adjudication.** It reports correlations in public records. It is
  not a FOCI determination, a sanctions hit, or a finding of non-compliance, and the notice says
  so in the body rather than in fine print.
- **OFAC matching is by name similarity only.** Real adjudication uses identifiers the tool does
  not have. A match is a question.
- **CIK resolution is conservative** (≥0.86 similarity with a matching first token). It will
  miss subsidiaries that file under an unrelated name before it will attribute the wrong
  company's filings.
- **Beneficial ownership behind a secrecy-jurisdiction vehicle is not resolvable from public
  data.** The tool flags the vehicle; identifying who is behind it needs commercial registry
  data or DCSA.
- **Government sources have real latency.** FPDS lags award execution; SAM registration data is
  self-certified.

## Layout

```
foci_screen/
  config.py        credentials, guards, connector availability
  models.py        Contract, Entity, Document, Change, Signal, Finding
  httpclient.py    rate limiting, retry, on-disk cache, OS trust store
  store.py         SQLite snapshots, diffing, findings, notification log
  pipeline.py      orchestration (injected deps — same code serves an API)
  cli.py           argparse entry point
  connectors/      usaspending, fpds, sec_edgar, registries, webwatch
  risk/            lexicon (jurisdictions, terms, clauses), engine (rules, scoring)
  notify/          render (.eml, text, html), gmail (guarded delivery)
tests/             45 offline tests
tools/             positive_control.py
```

See [ROADMAP.md](ROADMAP.md) for the path to an API, a web application, or a desktop build.
