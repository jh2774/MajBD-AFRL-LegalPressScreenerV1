# Where this goes next

You asked how to grow this into an API, a website, or a native program. Short answer: **build
the API, and make the website a thin client on top of it. Skip the native program.** Reasoning
and sequencing below.

---

## What the current design already gives you

`Screener.run()` takes its dependencies by injection (`config`, `http`, `store`) and returns
plain dataclasses with `.to_dict()`. Nothing in the screening core imports a CLI, a web
framework, or an ORM. That was deliberate: the same call that backs `foci-screen screen` backs
a request handler or a queue worker without modification.

The parts that are genuinely reusable, and the parts that are not:

| Component | State | Still outstanding for multi-user |
|---|---|---|
| `risk/` rules and lexicons | Reused unchanged | Tenant-specific weights |
| `connectors/` | Reused unchanged | Shared rate-limit budget across tenants |
| `models.py` | Reused unchanged | -- |
| `pipeline.py` | Runs as a job | Cancellation |
| `store.py` | SQLite **or** Postgres, tenant-scoped | -- |
| `httpclient.py` disk cache | Per-process, ephemeral on Render | Redis or S3 if cache hit rate matters |
| `cli.py` | Kept — same core, different front end | -- |

---

## Phase 1 - Make it a service — **BUILT**

The blocker was never the API surface, it was that a screen takes minutes and holds an HTTP
connection. Screening is now a job.

**Storage.** `store.py` speaks SQLite or Postgres from one schema; a `postgres://` DSN switches
dialect. Runs, findings, notices and watchlists carry `tenant_id`. Snapshots deliberately do
not — a document's hash is a fact about the world, not about who is watching, so ten tenants
screening the same prime fetch its 10-K once between them. Opening an existing v0.1 database
migrates it in place.

**Jobs.** RQ with Redis; without `REDIS_URL` screens run in a background thread instead, which
is right for a laptop and wrong for a deployment. One job per *run* rather than per entity —
per-entity was the plan, but a run is the unit a user cancels and polls, and splitting it would
mean reassembling progress from N jobs to answer one status call. Worth revisiting when a
single run's entity count gets large enough to matter.

**API.** FastAPI. All nine routes from the original sketch, plus `GET /v1/screens`,
`GET /v1/notices`, `POST /v1/notices/{id}/reject`, and watchlist run/deactivate.

**Scheduling.** `python -m foci_screen.scheduler` from cron sweeps active watchlists. It
refuses to run without a queue rather than enqueueing work that would die with the process.

**Auth.** API keys as `tenant:key` pairs in the environment, not OAuth2. A key table needs a
bootstrapping route to mint the first key, and that route is the most attacked surface an API
like this has; rotation is an env change and a restart, which is an acceptable trade for an
analyst tool. It **fails closed** — no keys configured means every authenticated route returns
503. Revisit if this ever needs human SSO or per-user attribution stronger than a shared key.

**Deployment.** `render.yaml` defines Postgres, Redis, API, worker and cron. Two images: only
the worker carries Chromium.

### What Phase 1 did not do

- **No `documents` table.** Snapshot text still lives on the `snapshots` row. The sharing
  benefit is already there; splitting the table is a normalisation exercise with no user
  visible payoff yet.
- **No per-tenant rate-limit budget.** All tenants share one QPS ceiling against the government
  APIs. With a handful of tenants this is fine. With enough of them, one tenant's large screen
  will starve everyone else's, and the limiter needs to become a shared token bucket in Redis.
- **No cancellation.** A queued run cannot be stopped.

## Phase 2 - Web application — **BUILT**

Thin client, no business logic in the browser. Served from the API image at `/`.

Built: unified search over contractors, awards, contracting officers and agencies; an overview
dashboard; contractor, officer, agency and award pages; and the notice review queue with
approve/reject and reasons captured.

**Not React.** Three static files, vanilla DOM, hand-rolled charts. The interactivity is modest,
and a build step plus a CDN script tag would both be liabilities on a network that blocks
outside origins — which describes a fair number of the environments this would run in. Revisit
if the rule-tuning screens land, since those are genuinely stateful.

The officer page turned out to be the one worth having: it answers *who would this notice go to,
and what else are they responsible for?*, which is the question a KO actually needs settled
before acting.

### Phase 2 remainder

1. ~~**Snapshot diff view.**~~ **Done.** Revisions are retained content-addressed by hash and
   pruned nightly to the 20 most recent per document; `GET /v1/documents/diff` returns
   structured lines rather than a diff string, so a line whose own text begins with `-` cannot
   be misread as a deletion. A first observation is flagged `baseline` — everything reads as
   new, which is not the same claim as the contractor having changed something.

   Signals now carry `document_key`, stamped centrally in `evaluate_documents` rather than by
   each of the ten rule constructors, and `GET /v1/documents/diff?entity_key=` marks the lines
   a rule fired on.

   Matching a signal to a line is a **heuristic**, not an exact mapping: evidence is a
   fixed-width window around the matched term, so it neither contains nor is contained by the
   line reliably — the test is a shared 40-character run. It is deliberately biased toward
   under-marking. Lines under 25 characters are never marked, because a heading collides with
   half the snippets on a site, and pointing a reviewer at the wrong sentence is worse than
   pointing at nothing. Carrying the match offset through from the lexicon would make this
   exact; it would mean threading a position through every rule.
2. ~~**Editable notice body.**~~ **Done**, along with three gaps it exposed in the approval gate:
   edits used to overwrite the generated text (now kept as `original_body_text`, shown as a
   diff); a rejected notice could be approved afterwards (now 409, with the status also guarded
   in the `UPDATE` so two reviewers cannot race); and an edit could delete the limitations
   statement (now refused).

   Remaining here: **per-person attribution.** Decisions record `api-key:<tenant>` because keys
   are shared. For an approval trail on notices to federal officials, that should become the
   signed-in reviewer — which is the SSO item under Phase 1 auth, not a UI change.
3. **Rule tuning.** Expose `BASE_WEIGHTS`, jurisdiction multipliers and damping factors as
   per-tenant configuration. Analysts will want to tune them without a deploy — and rejection
   reasons are now being captured, which is the data that makes tuning more than guesswork.
4. **Server-side pagination.** Search caps at 100 rows and the UI renders all of them. Fine at
   current volumes, wrong after a few thousand awards.

## Phase 3 - Native program

**Recommendation: don't**, unless a specific customer requires an air-gapped or
network-isolated deployment.

The tool's whole value comes from continuously polling internet-hosted government APIs, and
the natural deployment is a server that runs nightly. A desktop app that only screens while
someone has it open inverts that. If a customer does require local operation, the better
answer is a self-hosted container of the Phase 1 service with a local web UI - same code, no
second codebase.

If a true desktop build is genuinely required: Tauri wrapping the Phase 2 frontend, bundling
Python via PyInstaller as a sidecar. Budget the code-signing and auto-update work, which will
exceed the app work.

---

## What to fix before any of that

Ordered by how much they limit the tool today.

1. **Get a USPTO API key.** Recorded `SECURITY INTEREST` conveyances are the single strongest
   IP-collateralisation evidence available, and the screen currently cannot see them. This is
   free and is the highest value-per-effort item on the list by a wide margin.
2. **Get a SAM.gov API key.** Adds the registered ownership chain (immediate and highest-level
   owner) - the most direct FOCI structural signal there is - and an authoritative KO contact
   to corroborate FPDS.
3. **Headless browser for investor-relations pages — built, then partly withdrawn.** Playwright
   in the worker, applied only to hosts that fail the plain fetch. The v0.3 numbers
   (`investors.lockheedmartin.com` 0 → 905 characters, `investors.leidos.com` 0 → 1,952) came
   from a browser disguised as a person's Chrome, with bot-detection evasion switched on. That
   was removed in v0.7. Identified honestly, both hosts return 403 from Akamai, so they are not
   read. The browser still helps hosts that merely need JavaScript rendering rather than
   refusing automation.

   **The legitimate route to those pages is permission, not rendering.** Most IR platforms
   (Q4, Notified, EQS) offer RSS or email alerts for press releases, and wire services (PR
   Newswire, Business Wire, GlobeNewswire) syndicate the same announcements with licensed
   feeds. A feed connector is the next step if IR-page latency ahead of EDGAR matters. Do not
   put the disguise back.

   Chasing the IR problem did turn up a bug that stands: page normalisation trusted `<main>`,
   and Lockheed's newsroom keeps a nav rail there with the press releases outside it, so 150KB
   of HTML became 97 characters of menu labels. That fix took the page to 3,720 characters and
   improved every watched site.
4. **State-level UCC-1 search.** Where IP liens are perfected outside USPTO. No national API;
   Delaware, California and New York cover most of it. Commercial aggregators exist.
5. ~~**Widen contract coverage.**~~ **Done for subcontractors.** `--subcontractors N` screens
   suppliers under the primes, via `spending_by_award` with `subawards: true` (the endpoint the
   roadmap guessed at does not exist). Ranked by date, not dollars, because subaward values are
   self-reported and unreliable. See the README for the three data caveats.

   Still open here: **ranking by risk rather than dollars for primes.** Prime selection is
   still `ORDER BY obligated DESC`. A risk-ordered population would use prior findings, sector,
   and ownership-change signals — it needs the disposition data from item 7 to be worth
   anything.

   Also open: **distinguishing sole proprietors properly.** The individual test is a name
   heuristic. SAM.gov's entity registration states whether a registrant is a sole proprietor,
   which is authoritative; that needs `SAM_API_KEY` (item 2).
6. ~~**Entity resolution.**~~ **Done for UEI → CIK**, which is the mapping that bites: EDGAR
   full-text search is constrained by CIK, so a wrong answer returns another registrant's
   exhibits rather than nothing. `entity_links` stores the resolution with its confidence, and
   a reviewer can confirm, correct or reject it; both verdicts outrank the matcher permanently,
   and rejection actively stops attribution rather than merely leaving it unreviewed.

   Still open: **CAGE and CRD**. IAPD adviser matching is still by name each run, and there is
   no CAGE crosswalk at all. Both want SAM.gov (item 2) as the authoritative seed rather than
   another similarity score.
7. ~~**Feedback loop.**~~ **Done.** Verdicts are recorded per signal (`POST /v1/dispositions`),
   keyed on a hash of rule plus evidence so rewording a rule does not orphan them, and
   `GET /v1/rules/precision` reports precision per rule, worst first. The UI collects them
   under each signal, where the reviewer is already reading the evidence.

   What this now unblocks, and could not be attempted before:

   - **Retiring rules.** A rule at 0% precision over a meaningful sample is not a rule, it is
     noise with a weight attached. The page sorts worst-first for exactly this.
   - **Tuning weights against evidence** rather than judgement. Every number in
     `risk/engine.py` is currently an assumption.
   - **Risk-ordered screening** (item 5 remainder): ordering the population by predicted risk
     needs a target variable, and this is it.

   Needed before any of that is statistically meaningful: enough verdicts. A handful tells you
   nothing, and the page says "not measured" rather than implying otherwise.

---

## Compliance and legal, before you go multi-user

Not code, but it will shape the code.

- **Unsolicited email to federal contracting officers about named companies** is the part with
  real exposure. Keep the human approval gate. Consider whether notices should go to your own
  contracts or security organisation rather than directly to a KO - routing internally first
  is both safer and likelier to be acted on.
- **Defamation risk is real.** "Company X pledged its patents to a Chinese investor" is a
  factual assertion about a named business. The current notices attribute every claim to an
  openable source and state their limits; keep that discipline as the language evolves.
- **Terms of service.** USAspending, FPDS, EDGAR and OFAC are open. SAM.gov's API terms bind
  redistribution. Company websites are crawled under `robots.txt` (RFC 9309, enforced on both
  fetch paths since v0.7) and rate limits, with an identifying User-Agent and no evasion of bot
  management. `robots.txt` is a floor, not a licence: a site's terms of use can still restrict
  automated collection. Review the terms of any site added to a watchlist at scale.
- **CUI.** Everything the tool ingests today is public. The moment a customer wants it joined
  against non-public holdings (DCSA records, internal supplier data), hosting requirements
  change - FedRAMP, IL4/5. Decide this before choosing a cloud, not after.
