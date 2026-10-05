/* foci-screen web client.
 *
 * No framework and no CDN, deliberately. This ships inside the API image and
 * may run on networks that block outside origins; a build step and a remote
 * script tag would both be liabilities. The interactivity here is modest
 * enough that vanilla DOM is not a hardship.
 *
 * Everything from the API is escaped on the way into the DOM. Award
 * descriptions and company names are third-party text — government data is not
 * the same thing as trusted data.
 */
"use strict";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const CATEGORY_LABEL = {
  FOCI: "Foreign ownership / control",
  IP_COLLATERAL: "IP pledged as collateral",
  IP_TRANSFER: "IP transfer / distress",
  SANCTIONS: "Sanctions screening",
  STRUCTURE: "Corporate structure",
};

const view = document.getElementById("view");
const keyDialog = document.getElementById("key-dialog");

/* ----------------------------------------------------------------- helpers */

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const money = (n) => {
  const v = Number(n) || 0;
  if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
  return "$" + v.toFixed(0);
};

const num = (n) => (Number(n) || 0).toLocaleString();
const day = (s) => (s ? String(s).slice(0, 10) : "—");
const sevClass = (s) => (SEVERITIES.includes(s) ? `sev-${s}` : "sev-none");

/* An entity with contracts but no finding was screened and produced nothing at
 * or above the run's threshold. Labelling that "not screened" asserts the
 * opposite of what happened, which in a risk tool is the worst kind of wrong. */
const sevTag = (s) =>
  `<span class="sev ${sevClass(s)}">${esc(s || "no findings")}</span>`;

const linkEntity = (key, name) =>
  `<a href="#/entity/${encodeURIComponent(key)}">${esc(name || key)}</a>`;

function setBusy(label) {
  view.innerHTML = `<div class="spinner">${esc(label || "Loading…")}</div>`;
}

function showError(err) {
  view.innerHTML = `<div class="error"><strong>${esc(err.title || "Error")}</strong>
    <div class="muted" style="margin-top:6px">${esc(err.message || err)}</div></div>`;
}

/* --------------------------------------------------------------- API client */

const KEY_STORAGE = "foci.apikey";
let apiKey = "";
try {
  apiKey = localStorage.getItem(KEY_STORAGE) || "";
} catch {
  // Private mode or blocked storage: the key just will not persist.
}

async function api(path) {
  const res = await fetch(path, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
  });
  if (res.status === 401) {
    throw { title: "Not authorised", message: "The API key is missing or wrong. Set it from the top right." };
  }
  if (res.status === 503) {
    // The server's detail names the variable and how to set it; repeating a
    // vaguer version here just hides the fix.
    let detail = "The server is refusing authenticated requests.";
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    throw { title: "API is closed", message: detail };
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    // `status` so a caller can tell "nothing here" from "something broke".
    throw { title: "Request failed", message: detail, status: res.status };
  }
  return res.json();
}

/* PUT and DELETE, which the rule settings need. Shares apiPost's error shape so
 * callers handle failures the same way whatever the verb. */
async function apiSend(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    throw { title: "Request failed", message: detail };
  }
  return res.status === 204 ? null : res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    throw { title: "Request failed", message: detail };
  }
  return res.json();
}

/* -------------------------------------------------------------------- charts
 *
 * Hand-rolled SVG. A charting library would be a megabyte and an outside
 * origin for three chart types, none of which are hard.
 */

/* Horizontal bars are HTML, not SVG. An SVG viewBox scaled to the container
 * stretches its own text with it, which at container width made an 11px label
 * render about 60px tall. A grid row with a percentage-width fill is simpler,
 * responsive for free, and keeps text at the document's own size. */
function barChart(rows, opts = {}) {
  const { label, value, color, format = money, href } = opts;
  if (!rows.length) return `<div class="empty">Nothing to chart yet.</div>`;

  const max = Math.max(...rows.map((r) => Number(r[value]) || 0), 1);

  return `<div class="bars">${rows
    .map((r) => {
      const v = Number(r[value]) || 0;
      const pct = Math.max((v / max) * 100, 1.2); // keep tiny values visible
      const fill = typeof color === "function" ? color(r) : color || "var(--accent)";
      const text = esc(r[label] ?? "");
      return `<div class="bar-row">
          <div class="bar-label">${href ? `<a href="${href(r)}">${text}</a>` : text}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${pct.toFixed(1)}%;background:${fill}"></div>
          </div>
          <div class="bar-value">${esc(format(v))}</div>
        </div>`;
    })
    .join("")}</div>`;
}

function severityChart(counts) {
  const rows = SEVERITIES.filter((s) => counts[s]).map((s) => ({
    name: s.toUpperCase(),
    n: counts[s],
  }));
  if (!rows.length) return `<div class="empty">No entities screened yet.</div>`;
  return barChart(rows, {
    label: "name",
    value: "n",
    format: num,
    color: (r) => `var(--${r.name.toLowerCase()})`,
  });
}

function categoryChart(counts) {
  const rows = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ name: CATEGORY_LABEL[k] || k, n: v }));
  if (!rows.length) return `<div class="empty">No signals recorded yet.</div>`;
  return barChart(rows, { label: "name", value: "n", format: num });
}

function sparkline(points) {
  if (points.length < 2) {
    return `<div class="empty">Not enough history yet — this fills in as screens accumulate.</div>`;
  }
  const w = 600;
  const h = 90;
  const max = Math.max(...points.map((p) => p.n), 1);
  const step = w / (points.length - 1);
  const coords = points.map((p, i) => [i * step, h - (p.n / max) * (h - 14) - 6]);

  const line = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w},${h} L0,${h} Z`;
  const dots = coords
    .map(([x, y], i) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5"
        fill="var(--accent)"><title>${esc(points[i].day)}: ${points[i].n}</title></circle>`)
    .join("");

  return `<svg class="chart" viewBox="0 0 ${w} ${h}" height="${h}">
      <path d="${area}" fill="var(--accent-soft)"></path>
      <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2"></path>
      ${dots}
    </svg>
    <div class="legend"><span class="muted">${esc(points[0].day)}</span>
      <span class="muted" style="margin-left:auto">${esc(points[points.length - 1].day)}</span></div>`;
}

/* --------------------------------------------------------------- components */

function statCard(label, value) {
  return `<div class="card stat"><div class="value">${esc(value)}</div>
          <div class="label">${esc(label)}</div></div>`;
}

/* The API returns the 200 best-funded awards and the true count alongside.
 * Rendering the page without saying it is a page invites the reader to count
 * the rows and believe the answer. */
function truncationNote(shown, total) {
  if (!total || !shown || shown >= total) return "";
  return `<div class="muted" style="font-size:12px;margin-bottom:10px">
    Showing the ${num(shown)} largest of ${num(total)} awards. The totals above
    cover all ${num(total)}.</div>`;
}

/* The award record as it moved, not as it stands. The contracts table holds
 * only the present value — once a novation is written over the old contractor,
 * nothing on that row says an award changed hands. */
const CHANGE_IS_RISK = new Set([
  "entity_key", "recipient_uei", "country_of_incorporation",
  "recipient_country", "foreign_owned",
]);

function recordChanges(changes) {
  if (!changes.length) return "";
  const rows = changes.map((ch) => `
    <tr>
      <td>${day(ch.observed_at)}</td>
      <td>${esc(ch.label || ch.field)}</td>
      <td class="mono">${esc(ch.old_value || "—")}</td>
      <td class="mono">${esc(ch.new_value || "—")}</td>
      <td>${CHANGE_IS_RISK.has(ch.field)
             ? '<span class="pill warn">structural</span>'
             : '<span class="pill">administrative</span>'}</td>
    </tr>`).join("");
  return `
    <div class="card">
      <h2>Changes to the award record</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        What moved between screens. A contractor or UEI changing is a novation;
        a country of incorporation changing is the most direct structural
        indicator in the contract record. Both are written over in place, so
        this is the only place the previous value survives.</div>
      <table>
        <thead><tr><th>Seen</th><th>Field</th><th>Was</th><th>Now</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function contractsTable(contracts) {
  if (!contracts.length) return `<div class="empty">No awards recorded.</div>`;
  const rows = contracts
    .map(
      (c) => `<tr>
        <td><a href="#/contract/${encodeURIComponent(c.contract_key || c.piid)}"
               class="mono">${esc(c.piid || c.contract_key)}</a>
          ${c.is_subaward
            ? `<div class="muted" style="font-size:11.5px">subaward under
                 <span class="mono">${esc(c.prime_award_id || "?")}</span></div>`
            : ""}</td>
        <td>${linkEntity(c.entity_key, c.entity_name)}</td>
        <td>${esc(c.sub_agency || c.agency || "—")}</td>
        <td>${esc(c.psc_description || c.naics_description || "—")}</td>
        <td>${c.ko_email ? `<a href="#/officer/${encodeURIComponent(c.ko_email)}">${esc(c.ko_name || c.ko_email)}</a>` : '<span class="muted">unresolved</span>'}</td>
        <td class="num">${esc(money(c.amount))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>PIID</th><th>Contractor</th><th>Agency</th><th>Requirement</th>
      <th>Contracting officer</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function entitiesTable(entities, opts = {}) {
  if (!entities.length) return `<div class="empty">No contractors recorded.</div>`;
  const showScreened = opts.showScreened !== false;
  const rows = entities
    .map(
      (e) => `<tr>
        <td>${linkEntity(e.entity_key, e.entity_name)}
          ${e.foreign_owned ? '<span class="pill warn">foreign owned</span>' : ""}</td>
        <td>${sevTag(e.severity)}</td>
        <td class="num">${esc(num(e.contract_count))}</td>
        <td class="num">${esc(money(e.obligated))}</td>
        ${showScreened ? `<td class="muted">${esc(day(e.last_screened))}</td>` : ""}
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Contractor</th><th>Latest severity</th><th class="num">Awards</th>
      <th class="num">Obligated</th>${showScreened ? "<th>Screened</th>" : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function officersTable(officers) {
  if (!officers.length) return `<div class="empty">No contracting officers resolved.</div>`;
  const rows = officers
    .map(
      (o) => `<tr>
        <td><a href="#/officer/${encodeURIComponent(o.ko_email)}">${esc(o.ko_name || o.ko_email)}</a>
            <div class="muted mono">${esc(o.ko_email)}</div></td>
        <td><span class="pill">${esc(o.ko_confidence || "unknown")} confidence</span></td>
        <td class="num">${esc(num(o.contract_count))}</td>
        <td class="num">${esc(num(o.entity_count))}</td>
        <td class="num">${esc(money(o.obligated))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Officer</th><th>Attribution</th><th class="num">Awards</th>
      <th class="num">Contractors</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function agenciesTable(agencies) {
  if (!agencies.length) return `<div class="empty">No agencies recorded.</div>`;
  const rows = agencies
    .map(
      (a) => `<tr>
        <td><a href="#/agency/${encodeURIComponent(a.agency)}">${esc(a.agency)}</a>
          ${a.sub_agency ? `<div class="muted">${esc(a.sub_agency)}</div>` : ""}</td>
        <td class="num">${esc(num(a.contract_count))}</td>
        <td class="num">${esc(num(a.entity_count))}</td>
        <td class="num">${esc(num(a.officer_count))}</td>
        <td class="num">${esc(money(a.obligated))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Agency</th><th class="num">Awards</th><th class="num">Contractors</th>
      <th class="num">Officers</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

const VERDICT_LABEL = {
  true_positive: "confirmed",
  false_positive: "false positive",
  unclear: "unclear",
};

/* `opts.entityKey` turns on the verdict controls. A verdict is the only
 * labelled data this tool ever produces, so it is collected where a reviewer
 * is already reading the evidence rather than on a separate screen. */
function signalList(signals, opts = {}) {
  if (!signals || !signals.length) return `<div class="empty">No signals.</div>`;
  const verdicts = opts.dispositions || {};
  return signals
    .map((s) => {
      const recorded = verdicts[s.signal_id];
      const controls =
        opts.entityKey && s.signal_id
          ? `<div class="verdict" data-signal="${esc(s.signal_id)}"
                  data-rule="${esc(s.rule_id)}" data-category="${esc(s.category || "")}"
                  data-severity="${esc(s.severity || "")}">
               ${recorded
                 ? `<span class="pill ${recorded.verdict === "false_positive" ? "warn" : ""}">
                      marked ${esc(VERDICT_LABEL[recorded.verdict] || recorded.verdict)}
                      ${recorded.decided_by ? `by ${esc(recorded.decided_by)}` : ""}</span>
                    <button class="verdict-btn ghost" data-verdict="">Change</button>`
                 : `<span class="muted" style="font-size:12px">Was this right?</span>
                    <button class="verdict-btn" data-verdict="true_positive">Confirmed</button>
                    <button class="verdict-btn" data-verdict="false_positive">False positive</button>
                    <button class="verdict-btn ghost" data-verdict="unclear">Unclear</button>`}
             </div>`
          : "";
      return `<div class="signal s-${esc(s.severity)}">
        <div class="title">${sevTag(s.severity)} ${esc(s.title)}
          ${s.is_new ? '<span class="pill warn">new</span>' : ""}</div>
        <div class="why">${esc(s.rationale)}</div>
        ${s.evidence ? `<div class="evidence">${esc(s.evidence)}</div>` : ""}
        <div class="muted" style="margin-top:6px;font-size:12px">
          ${esc(s.rule_id)} · ${esc(s.source)}
          ${s.source_url ? ` · <a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">source</a>` : ""}
        </div>
        ${controls}</div>`;
    })
    .join("");
}

/* Wires the verdict buttons inside `root` for one entity. */
function wireVerdicts(root, entityKey, runId) {
  root.querySelectorAll(".verdict").forEach((box) => {
    box.querySelectorAll(".verdict-btn").forEach((btn) => {
      btn.onclick = async () => {
        const verdict = btn.dataset.verdict;
        if (!verdict) {            // "Change" — offer the choices again
          box.innerHTML =
            `<span class="muted" style="font-size:12px">Was this right?</span>
             <button class="verdict-btn" data-verdict="true_positive">Confirmed</button>
             <button class="verdict-btn" data-verdict="false_positive">False positive</button>
             <button class="verdict-btn ghost" data-verdict="unclear">Unclear</button>`;
          wireVerdicts(box.parentElement, entityKey, runId);
          return;
        }
        btn.disabled = true;
        try {
          await apiPost("/v1/dispositions", {
            signal_id: box.dataset.signal,
            entity_key: entityKey,
            rule_id: box.dataset.rule,
            category: box.dataset.category,
            severity: box.dataset.severity,
            verdict,
            run_id: runId || "",
          });
          box.innerHTML = `<span class="pill ${verdict === "false_positive" ? "warn" : ""}">
            marked ${esc(VERDICT_LABEL[verdict])}</span>`;
        } catch (e) {
          btn.disabled = false;
          box.insertAdjacentHTML(
            "beforeend",
            `<span class="muted" style="font-size:12px"> — ${esc(e.message)}</span>`);
        }
      };
    });
  });
}

/* ------------------------------------------------------------------- views */

/* An empty database and a closed API produce the same blank-looking page, and
 * reading eight charts of zeros to tell them apart is not reasonable. Say
 * which one it is, and how to leave the state. */
function firstRunPanel() {
  return `
    <div class="page-head">
      <h1>Overview</h1>
      <div class="sub">There is nothing in this database${
        storageIsEphemeral ? " right now" : " yet"}.</div>
    </div>

    ${storageIsEphemeral ? `
    <div class="card" style="border-left:3px solid var(--high)">
      <h2>If you screened something and it is not here, this is why</h2>
      <p class="muted">This deployment keeps its database on a filesystem that
      is replaced on every deploy — and on an idle instance that spins down and
      comes back up. A screen that ran and finished is discarded along with it,
      which looks from here exactly like a screen that never ran.</p>
      <p class="muted">Running another one will work, and will be lost the same
      way. <strong>Fix the storage first</strong> — see
      <span class="mono">DEPLOY.md</span>: attach a persistent disk and point
      <span class="mono">FOCI_DB</span> at it (one service, needs a paid
      instance type), or create a Postgres instance and set
      <span class="mono">DATABASE_URL</span> (works on any plan).</p>
    </div>` : ""}

    <div class="card">
      <h2>No screens have run</h2>
      <p class="muted">The API is answering and your key works — this database
      is simply empty, which is a different thing from a screen that found
      nothing. Start one:</p>
      <div class="code">curl -X POST ${esc(location.origin)}/v1/screens \\
  -H "Authorization: Bearer &lt;your key&gt;" \\
  -H "Content-Type: application/json" \\
  -d '{"agency":"Department of Defense","months_back":6,"max_awards":25,"max_entities":5}'</div>
      <p class="muted">Expect minutes, not seconds. The first run is a
      <strong>baseline</strong>: it records what each document looks like now,
      and damps its findings accordingly. The second run onward is where the
      change detection earns its keep — so a database that gets discarded
      between runs never produces the thing this tool is for.</p>
      <p class="muted">Findings are scoped to the tenant that wrote them. The
      command line writes as <code>default</code>, so screens run there are
      invisible here unless your key names that tenant.</p>
    </div>`;
}

/* With a portfolio saved, the overview is that portfolio. Without one it shows
 * everything in the database. */
async function viewOverview() {
  const portfolioKey = savedPortfolioKey();
  if (portfolioKey) {
    // The portfolio *is* the dashboard now.
    return viewPortfolio();
  }

  setBusy("Loading overview…");
  const d = await api("/v1/overview");
  const t = d.totals;

  if (!t.contracts && !t.entities && !t.notices_pending) {
    // The panel's wording depends on whether this deployment keeps anything,
    // and the boot-time health call may not have landed yet.
    await refreshHealth();
    view.innerHTML = firstRunPanel();
    return;
  }

  renderFullDatabase(d);
}

function renderFullDatabase(d) {
  const t = d.totals;

  view.innerHTML = `
    <div class="page-head">
      <h1>Overview</h1>
      <div class="sub">What has been screened, and what changed.</div>
    </div>

    <div class="card about">
      <h2>What FOCI-Screener does</h2>
      <p class="muted">FOCI-Screener watches the companies that hold federal
      contracts for signs of <strong>Foreign Ownership, Control or Influence
      (FOCI)</strong> and for risks to their intellectual property, such as
      patents pledged as loan collateral or sold off. It looks for
      <strong>changes</strong>: a contractor that has always been a Delaware
      company is not news, but one that filed a notice last week about a new
      offshore investor is.</p>
      <ol class="muted">
        <li><strong>Find the contracts.</strong> For a chosen agency it pulls
        recent awards from USAspending and FPDS, with the winning company, its
        parent, and the contracting officer responsible for each award.</li>
        <li><strong>Check the contractors.</strong> For each company it reads
        SEC filings, investment-adviser records, the OFAC sanctions list, SAM.gov
        registrations, USPTO patent assignments, and the company's own press and
        investor pages.</li>
        <li><strong>Track what changed.</strong> Every document it reads is
        stored and fingerprinted. On later screens, rules run against what is
        new, so findings point to fresh disclosures rather than old news.</li>
        <li><strong>Draft a notice.</strong> When a contractor's findings are
        serious enough, it drafts a notice to the contracting officer named on
        the award. A person reviews every notice, and nothing is emailed
        automatically.</li>
      </ol>
      <p class="muted">The first screen of an agency sets a baseline; screens
      after that show what moved. Results are leads for an analyst to review,
      drawn from public records, not a FOCI determination.</p>
    </div>

    <div class="grid cols-4">
      ${statCard("Obligated", money(t.obligated))}
      ${statCard("Awards", num(t.contracts))}
      ${statCard("Contractors", num(t.entities))}
      ${statCard("Notices pending", num(t.notices_pending))}
    </div>

    <div class="grid cols-2">
      <div class="card">
        <h2>Contractors by latest severity</h2>
        ${severityChart(d.severity)}
      </div>
      <div class="card">
        <h2>Signals by category</h2>
        ${categoryChart(d.categories)}
      </div>
    </div>

    <div class="card">
      <h2>Findings recorded per day</h2>
      ${sparkline(d.findings_by_day)}
    </div>

    <div class="card">
      <h2>Largest contractors screened</h2>
      ${barChart(d.top_entities, {
        label: "entity_name",
        value: "obligated",
        href: (r) => `#/entity/${encodeURIComponent(r.entity_key)}`,
        color: (r) => (r.severity ? `var(--${r.severity})` : "var(--info)"),
      })}
      <div class="legend">
        ${SEVERITIES.map(
          (s) => `<span class="item"><span class="dot" style="background:var(--${s})"></span>${esc(s)}</span>`
        ).join("")}
      </div>
    </div>

    <div class="card">
      <h2>Screens that produced this</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        Everything above came from these runs. Open one to see what it read, or
        to remove it if it brought in contractors you did not mean to watch.</div>
      <div id="recent-runs" class="muted">Loading…</div>
    </div>

    <div class="card">
      <h2>Most recent findings</h2>
      ${entitiesTable(
        d.recent_findings.map((f) => ({
          entity_key: (f.entity || {}).key || (f.entity || {}).uei || (f.entity || {}).name,
          entity_name: (f.entity || {}).name,
          severity: f.severity,
          contract_count: (f.contracts || []).length,
          obligated: (f.contracts || []).reduce((a, c) => a + (c.award_amount || 0), 0),
          last_screened: f.generated_at,
        }))
      )}
    </div>`;

  loadRecentRuns();
}

/* Loaded after the page so a slow history call cannot hold up the dashboard. */
function loadRecentRuns() {
  api("/v1/screens?limit=10").then((r) => {
    const box = document.getElementById("recent-runs");
    if (!box) return;
    const runs = r.runs || [];
    box.innerHTML = runs.length
      ? `<table><thead><tr><th>Subject</th><th>Status</th><th>Started</th></tr></thead>
         <tbody>${runs.map((run) => `
           <tr>
             <td><a href="#/screens/${encodeURIComponent(run.run_id)}">${esc(run.agency || run.run_id)}</a></td>
             <td>${esc(run.status || "")}</td>
             <td>${day(run.started_at)}</td>
           </tr>`).join("")}</tbody></table>`
      : "No screens recorded.";
  }).catch(() => {
    const box = document.getElementById("recent-runs");
    if (box) box.textContent = "Could not load the run history.";
  });
}

/* Search reads the screened population, so a firm nobody has screened
 * correctly matches nothing. Saying only that is a dead end: the honest reply
 * is that this database has not looked at the firm yet, and an offer to go and
 * look. Without it the only route in was to screen a whole department and take
 * whoever turned up. */
function screenThisFirm(q) {
  if (!q) {
    return `<div class="card"><div class="empty">Type a contractor, award,
      officer or agency to search what has been screened.</div></div>`;
  }
  return `
    <div class="card">
      <h2>Nothing screened here matches “${esc(q)}”</h2>
      <p class="muted">Search covers what this database has already screened,
      so a firm it has never looked at will not appear. If “${esc(q)}” is a
      contractor, screen it now — this pulls its federal awards from
      USAspending and runs the full check.</p>
      <div class="actions">
        <button class="primary" id="screen-firm">Screen “${esc(q)}”</button>
      </div>
      <p class="muted" id="screen-firm-status" style="margin-top:10px"></p>
    </div>`;
}

function wireScreenFirm(q) {
  const button = document.getElementById("screen-firm");
  if (!button) return;
  const status = document.getElementById("screen-firm-status");
  button.onclick = async () => {
    button.disabled = true;
    status.textContent = "Starting…";
    try {
      const d = await apiPost("/v1/screens", {
        recipient: q, months_back: 12, max_awards: 25, max_entities: 3,
      });
      status.innerHTML = `Screening started (run <span class="mono">${esc(d.run_id)}</span>).
        This takes minutes — the contractor appears in search once it finishes.
        <a href="#/screens/${encodeURIComponent(d.run_id)}">Watch it</a>.`;
    } catch (e) {
      button.disabled = false;
      status.textContent = e.message || String(e);
    }
  };
}

async function viewSearch(q, kind) {
  document.getElementById("search-input").value = q;
  setBusy(`Searching for “${q}”…`);
  const d = await api(
    `/v1/search?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind || "all")}&limit=25`
  );

  const tabs = [
    ["all", "All"],
    ["entity", "Contractors"],
    ["contract", "Awards"],
    ["officer", "Officers"],
    ["agency", "Agencies"],
  ]
    .map(
      ([k, lbl]) =>
        `<button data-kind="${k}" class="${(kind || "all") === k ? "active" : ""}">${lbl}</button>`
    )
    .join("");

  const total =
    (d.entities || []).length + (d.contracts || []).length +
    (d.officers || []).length + (d.agencies || []).length;

  const section = (title, html, rows) =>
    rows && rows.length ? `<div class="card"><h2>${title}</h2>${html}</div>` : "";

  view.innerHTML = `
    <div class="page-head">
      <h1>${q ? `Results for “${esc(q)}”` : "Browse"}</h1>
      <div class="sub">${total} match${total === 1 ? "" : "es"}${q ? "" : " — showing the largest of each"}</div>
    </div>
    <div class="tabs">${tabs}</div>
    ${total === 0 ? screenThisFirm(q) : ""}
    ${section("Contractors", entitiesTable(d.entities || []), d.entities)}
    ${section("Awards", contractsTable(d.contracts || []), d.contracts)}
    ${section("Contracting officers", officersTable(d.officers || []), d.officers)}
    ${section("Agencies", agenciesTable(d.agencies || []), d.agencies)}`;

  view.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => {
      location.hash = `#/search/${encodeURIComponent(q)}/${b.dataset.kind}`;
    };
  });
  wireScreenFirm(q);
}

/* A screen takes minutes, so starting one from the search page and being given
 * nothing to look at is its own dead end. */
let screenPollTimer = null;

async function viewScreen(runId) {
  if (screenPollTimer) clearTimeout(screenPollTimer);
  const d = await api(`/v1/screens/${encodeURIComponent(runId)}`);
  const running = d.status === "running" || d.status === "queued";
  const stats = d.stats || {};

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Screen</div>
    <div class="page-head">
      <h1>${esc(d.agency || "Screen")}</h1>
      <div class="sub"><span class="mono">${esc(runId)}</span> · started ${day(d.started_at)}</div>
    </div>

    <div class="card">
      <h2>${esc(d.status || "unknown")}</h2>
      ${running
        ? `<p class="muted">Running. This page refreshes itself; a screen
           usually takes a few minutes.</p>
           <div class="code">${esc(d.progress || "starting…")}</div>`
        : ""}
      ${d.error ? `<div class="error"><strong>${esc(d.status)}</strong>
        <div class="muted" style="margin-top:6px">${esc(d.error)}</div></div>` : ""}
      ${!running && !d.error ? `<p class="muted">Finished ${day(d.finished_at)}.</p>` : ""}
    </div>

    ${Object.keys(stats).length ? `
    <div class="grid cols-4">
      ${statCard("Awards examined", num(stats.awards_examined))}
      ${statCard("Contractors", num(stats.entities_screened))}
      ${statCard("Findings", num(stats.findings))}
      ${statCard("Notices pending", num(stats.notices_pending))}
    </div>` : ""}

    ${(stats.notes || []).length ? `
    <div class="card">
      <h2>What the run could not read</h2>
      <ul class="muted" style="font-size:13px">
        ${stats.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    </div>` : ""}

    ${!running ? `
    <div class="card">
      <h2>Remove this screen</h2>
      <p class="muted">Everything this run put on the dashboard — its awards,
      findings and pending notices — goes with it. Useful when a screen brought
      in contractors you did not mean to watch. Stored document revisions are
      shared between screens and are left alone.</p>
      <div class="actions">
        <button class="ghost" id="screen-remove">Remove this screen's data</button>
      </div>
      <p class="muted" id="screen-remove-status" style="margin-top:10px"></p>
    </div>` : ""}

    ${!running ? `<div class="actions">
      <a class="linkish" href="#/">Back to the overview</a></div>` : ""}`;

  wireScreenRemove(runId);
  if (running) screenPollTimer = setTimeout(() => viewScreen(runId), 5000);
}

/* Two steps on purpose: the first shows what would go, the second does it.
 * The counts are the only way to tell a stray screen from the run holding a
 * quarter's work, and this cannot be undone. */
function wireScreenRemove(runId) {
  const button = document.getElementById("screen-remove");
  if (!button) return;
  const status = document.getElementById("screen-remove-status");
  let confirmed = false;

  button.onclick = async () => {
    try {
      if (!confirmed) {
        const d = await apiSend("DELETE", `/v1/screens/${encodeURIComponent(runId)}`);
        const counts = d.would_remove || {};
        const parts = Object.entries(counts)
          .filter(([, n]) => n > 0)
          .map(([k, n]) => `${num(n)} ${k.replace(/_/g, " ")}`);
        status.textContent = parts.length
          ? `This will delete ${parts.join(", ")}. It cannot be undone — click again to confirm.`
          : "This run recorded nothing. Click again to remove the run itself.";
        button.textContent = "Confirm removal";
        confirmed = true;
        return;
      }
      await apiSend("DELETE", `/v1/screens/${encodeURIComponent(runId)}?confirm=true`);
      location.hash = "#/";
    } catch (e) {
      status.textContent = e.message || String(e);
    }
  };
}

async function viewEntity(key) {
  setBusy("Loading contractor…");
  const [d, docs, verdicts, identity] = await Promise.all([
    // Nothing recorded is an answer, not a failure: a company named in a
    // portfolio but never screened still has a page — see viewUnscreened.
    api(`/v1/entities/${encodeURIComponent(key)}`)
      .catch((e) => { if (e.status === 404) return null; throw e; }),
    api(`/v1/documents?entity_key=${encodeURIComponent(key)}`).catch(() => ({ documents: [] })),
    api(`/v1/entities/${encodeURIComponent(key)}/dispositions`)
      .catch(() => ({ dispositions: {} })),
    api(`/v1/entities/${encodeURIComponent(key)}/identity`).catch(() => null),
  ]);
  if (!d) return viewUnscreened(key, identity);
  // Opened by the name typed into a portfolio, but screened under its UEI:
  // go to the page everything else is filed against.
  if (d.entity_key && d.entity_key !== key.toUpperCase()) {
    location.replace(`#/entity/${encodeURIComponent(d.entity_key)}`);
    return;
  }
  d.documents = docs.documents || [];
  d.dispositions = verdicts.dispositions || {};
  d.identity = identity;
  const e = d.entity || {};
  const f = d.latest_finding;

  const history = (d.history || []).map((h) => ({
    day: day(h.created_at),
    n: SEVERITIES.length - SEVERITIES.indexOf(h.severity),
  }));

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Contractor</div>
    <div class="page-head">
      <h1>${esc(e.name || key)}</h1>
      <div class="sub">
        ${f ? sevTag(f.severity) + ` score ${esc((f.total_score ?? 0).toFixed ? f.total_score.toFixed(1) : f.total_score)}` : sevTag(null)}
        ${e.uei ? ` · UEI <span class="mono">${esc(e.uei)}</span>` : ""}
        ${e.cik ? ` · CIK <span class="mono">${esc(e.cik)}</span>` : ""}
      </div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Awards", num(d.contract_count ?? (d.contracts || []).length))}
      ${statCard("Screens", num((d.history || []).length))}
    </div>

    ${(e.countries || []).length || (e.domains || []).length ? `<div class="card">
      <h3>Registration</h3>
      ${(e.countries || []).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}
      ${(e.domains || []).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}
      ${e.parent_name ? `<span class="pill">parent: ${esc(e.parent_name)}</span>` : ""}
    </div>` : ""}

    ${identityCard(d.identity, key)}

    ${formdPlaceholder()}

    ${f ? `<div class="card"><h2>Signals</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        Marking these is what makes rule weights measurable rather than assumed —
        see <a href="#/rules">rule precision</a>.</div>
      ${signalList(f.signals, { entityKey: key, dispositions: d.dispositions })}</div>` : ""}

    ${history.length > 1 ? `<div class="card">
      <h2>Severity over time</h2>
      <div class="muted" style="font-size:12px;margin-bottom:8px">
        Higher is worse. Each point is one screen.</div>
      ${sparkline(history)}
    </div>` : ""}

    <div class="card">
      <h2>Source documents</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        What the screen read. Documents with more than one revision can be
        diffed — that is where a change actually shows itself.</div>
      ${documentsTable(d.documents || [], key)}
    </div>

    ${addToPortfolioButton([key], "Watch this contractor on your dashboard.")}

    ${recordChanges(d.record_changes || [])}

    <div class="card">
      <h2>Awards</h2>
      ${truncationNote(d.contracts_shown, d.contract_count)}
      ${contractsTable(d.contracts || [])}
    </div>`;

  wireVerdicts(view, key, f ? f.run_id : "");
  wireIdentity(view, key);
  wireAddToPortfolio();
  // After the page is drawn: a company's first Form D check reads each of its
  // filings from EDGAR, and the rest of the page should not wait for that.
  loadFormD(key, e.name || key);
}

/* A company someone put in a portfolio by name, which this database has never
 * screened. It used to have no page at all — the portfolio linked to an error —
 * which made the one thing that needs no screen, picking its SEC record to
 * watch its fundraising, impossible for exactly the companies it is for. */
async function viewUnscreened(key, identity) {
  let name = (identity && identity.entity_name) || "";
  let inPortfolio = false;
  const saved = savedPortfolioKey();
  if (saved) {
    try {
      const p = await apiPost("/v1/portfolio", { key: saved });
      const row = p.companies.find(
        (c) => (c.entity_key || "").toUpperCase() === key.toUpperCase());
      if (row) {
        inPortfolio = true;
        name = name || row.entity_name;
      }
    } catch { /* the page works without the portfolio */ }
  }
  name = name || key;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> ›
      ${inPortfolio ? '<a href="#/portfolio">Portfolio</a> › ' : ""}Contractor</div>
    <div class="page-head">
      <h1>${esc(name)}</h1>
      <div class="sub"><span class="pill">not screened here</span>
        ${inPortfolio ? " · in your portfolio" : ""}</div>
    </div>

    <div class="card">
      <h2>Not screened on this site yet</h2>
      <p class="muted">Nothing is stored here about this company's federal awards. You
      can still pick its SEC record below and be told when it raises money — that
      needs no screen. To pull its awards from USAspending and run the full check,
      screen it; that takes a few minutes.</p>
      <div class="actions">
        <button class="primary" id="screen-firm">Screen “${esc(name)}”</button>
      </div>
      <p class="muted" id="screen-firm-status" style="margin-top:10px"></p>
    </div>

    ${identity ? identityCard(identity, key) : ""}

    ${formdPlaceholder()}

    ${inPortfolio ? "" : addToPortfolioButton([key], "Watch this company on your dashboard.")}`;

  wireScreenFirm(name);
  wireIdentity(view, key);
  wireAddToPortfolio();
  loadFormD(key, name);
}

/* --------------------------------------------- a contractor's own Form D */

/* Form D is the notice a company files when it raises money privately — the
 * contractor's own record, where Form ADV is the investment firms'. It is only
 * read once a person has picked which SEC filer the contractor is: EDGAR's
 * name list mixes in investment vehicles that merely bought shares in a
 * company, and a wrong pick would put their fundraising under this name. */
const FORMD_TITLE = "Private fundraising (SEC Form D)";

function formdPlaceholder() {
  return `<div class="card" id="formd-card"><h2>${FORMD_TITLE}</h2>
    <p class="muted">Checking…</p></div>`;
}

async function loadFormD(key, name, refresh = false) {
  const card = document.getElementById("formd-card");
  if (!card) return;
  if (refresh) card.querySelector("#fd-status")?.replaceChildren("Checking EDGAR for new filings…");
  let d;
  try {
    d = await api(`/v1/entities/${encodeURIComponent(key)}/formd${refresh ? "?refresh=true" : ""}`);
  } catch (e) {
    card.innerHTML = `<h2>${FORMD_TITLE}</h2>
      <p class="muted">${esc(e.message || String(e))}</p>`;
    return;
  }
  card.innerHTML = d.linked ? formdFilings(d) : formdPicker(d, name);
  wireFormD(card, key, name);
}

function formdPicker(d, name) {
  const link = d.link || {};
  return `
    <h2>${FORMD_TITLE}</h2>
    <p class="muted">When a private company sells shares to investors, it must file a
    short notice with the SEC — <strong>Form D</strong> — saying how much it is raising
    and how many investors bought in. To see and watch this contractor's, find its SEC
    record below and pick it.</p>
    <p class="muted" style="font-size:12.5px">Pick the company itself. EDGAR also lists
    <strong>investment vehicles</strong> named after a company — pools of money that only
    bought shares in it. Those are not the company, and their filings are not its
    fundraising. Picking one here also tells the screen which SEC filer this contractor is.</p>
    ${link.cik && link.status === "auto" ? `<p class="muted" style="font-size:12.5px">
      The screen matched this contractor by name to CIK ${esc(link.cik)}
      (${esc(link.matched_title || "")}), but nobody has checked that. Confirm it in the
      SEC identity card above, or pick below.</p>` : ""}
    ${d.suggested ? `<div class="notice-body" style="margin:10px 0">
      Before this company was screened, someone picked
      <strong>${esc(d.suggested.matched_title || "CIK " + d.suggested.cik)}</strong>
      (CIK ${esc(d.suggested.cik)}) as its SEC record. If this is the same company:
      <button class="fd-pick" style="margin-left:8px" data-cik="${esc(d.suggested.cik)}"
        data-name="${esc(d.suggested.matched_title || "")}">Use it here</button></div>` : ""}
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <input id="fd-q" value="${esc(name)}" aria-label="Company name to look up on EDGAR"
        style="flex:1;min-width:220px;padding:8px;border:1px solid var(--border);
        border-radius:8px;background:var(--surface-2);color:var(--text)">
      <button id="fd-search">Search EDGAR</button>
    </div>
    <div id="fd-results" style="margin-top:10px"></div>`;
}

const FORMD_LABEL = {
  company: "company",
  vehicle: "investment vehicle — not the company",
  person: "looks like a person",
};

function formdResults(results) {
  if (!results.length) {
    return '<p class="muted">Nothing on EDGAR by that name. Private companies that have never raised money from investors often have no SEC record at all.</p>';
  }
  return `<table><tbody>${results.map((r) => `
    <tr><td><strong>${esc(r.name)}</strong>
        <span class="pill${r.label === "company" ? "" : " warn"}">${esc(FORMD_LABEL[r.label] || r.label)}</span>
        <div class="muted" style="font-size:12px">CIK ${esc(r.cik)}${
          r.place ? " · " + esc(r.place) : ""}${
          r.incorporated_in ? " · incorporated in " + esc(r.incorporated_in) : ""}${
          r.form_d_count !== undefined ? ` · ${num(r.form_d_count)} Form D notice${r.form_d_count === 1 ? "" : "s"}` : ""}${
          (r.tickers || []).length ? " · traded as " + esc(r.tickers.join(", ")) : ""}</div></td>
      <td style="text-align:right;white-space:nowrap">
        ${r.label === "company"
          ? `<button class="fd-pick" data-cik="${esc(r.cik)}" data-name="${esc(r.name)}">This is the company</button>`
          : `<button class="ghost fd-pick" data-cik="${esc(r.cik)}" data-name="${esc(r.name)}"
               title="The label is a guess from the name; pick this only if you know better">Pick anyway</button>`}
      </td></tr>`).join("")}</tbody></table>`;
}

function formdFilings(d) {
  const s = d.snapshot || {};
  const filings = s.filings || [];
  const latest = filings.find((f) => f.read_ok !== false);
  const cell = (v) => (v === null || v === undefined ? "—" : v);
  const rows = filings.map((f) => f.read_ok === false ? `
    <tr><td>${esc(f.filed)}</td><td>${f.form === "D/A" ? "Update" : "New"}</td>
      <td colspan="4" class="muted">Could not be read here — open it on EDGAR.</td>
      <td><a class="linkish" href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">Open →</a></td></tr>` : `
    <tr>
      <td>${esc(f.filed)}</td>
      <td>${f.form === "D/A" ? "Update to an earlier notice" : "New fundraising"}
        ${f.business_combination ? '<span class="pill warn">tied to a merger or acquisition</span>' : ""}
        ${f.outside_us ? '<span class="pill warn">company address outside the U.S.</span>' : ""}</td>
      <td class="num">${f.offering_indefinite ? "no set total" : cell(f.offering_amount === null ? null : money(f.offering_amount))}</td>
      <td class="num"><strong>${cell(f.amount_sold === null ? null : money(f.amount_sold))}</strong></td>
      <td class="num">${cell(f.investors === null ? null : num(f.investors))}</td>
      <td>${esc(f.first_sale || "—")}</td>
      <td><a class="linkish" href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">Open →</a></td>
    </tr>`).join("");

  const people = latest ? (latest.related_persons || []) : [];
  return `
    <h2>${FORMD_TITLE}</h2>
    <p class="muted">Notices <strong>${esc(s.name || "")}</strong> (CIK ${esc(d.cik)}) filed
    with the SEC when it sold shares or other stakes to private investors. Each says how
    much the company set out to raise, how much it had raised by then, and how many
    investors had bought in. It does not name the investors. Wrong company? Change it in
    the SEC identity card above.</p>

    ${d.changes.length ? `<h3>New since this company was first checked</h3>
      ${d.changes.map((c) => `
        <div style="border-left:3px solid var(--accent);padding:4px 12px;margin:10px 0">
          <div><strong>${esc(c.headline)}</strong> <span class="muted" style="font-size:12px">· ${esc(day(c.detected_at))}</span></div>
          ${c.detail ? `<div>${esc(c.detail)}</div>` : ""}
          <div class="muted" style="font-size:12.5px"><em>What this is:</em> ${esc(c.explainer)}</div>
          <div class="muted" style="font-size:12.5px"><em>Where to look:</em> ${esc(c.where)}</div>
        </div>`).join("")}` : ""}

    ${filings.length ? `<table>
      <thead><tr><th>Filed</th><th>Notice</th><th class="num">Trying to raise</th>
        <th class="num">Raised so far</th><th class="num">Investors</th><th>First sale</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
      ${s.total_filings > filings.length ? `<p class="muted" style="font-size:12px">
        Showing the latest ${num(filings.length)} of ${num(s.total_filings)}; the rest are on EDGAR.</p>` : ""}`
      : `<p class="muted">This company has not filed a Form D. If it is in a portfolio with
         email alerts, the list will be told when it does.</p>`}

    ${people.length ? `<h3 style="margin-top:14px">People named on the latest notice</h3>
      <p class="muted" style="font-size:12.5px">Form D must list the company's directors and
      top executives, and anyone paid to promote the fundraising.</p>
      <ul style="margin:0;padding-left:20px">${people.map((p) => `<li>${esc(p.name)}
        <span class="muted">— ${esc(p.title || (p.roles || []).join(", ").toLowerCase() || "named")}${p.place ? ", " + esc(p.place) : ""}</span>
        ${p.outside_us ? '<span class="pill warn">address outside the U.S.</span>' : ""}</li>`).join("")}</ul>` : ""}

    <div class="actions">
      <a class="linkish" href="${esc(d.links.company)}" target="_blank" rel="noopener noreferrer">All of its SEC filings on EDGAR →</a>
      <button id="fd-refresh">Check EDGAR now</button>
    </div>
    <p class="muted" id="fd-status" style="font-size:12px">${
      d.refresh_detail ? esc(d.refresh_detail) + " " : ""}Last checked ${esc(day(s.checked_at))}.</p>`;
}

function wireFormD(card, key, name) {
  const refresh = card.querySelector("#fd-refresh");
  if (refresh) {
    refresh.onclick = () => { refresh.disabled = true; loadFormD(key, name, true); };
    return;
  }
  const input = card.querySelector("#fd-q");
  const out = card.querySelector("#fd-results");
  const wirePicks = (root) => root.querySelectorAll(".fd-pick").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "Saving…";
      try {
        await apiPost(`/v1/entities/${encodeURIComponent(key)}/identity`, {
          status: "confirmed",
          cik: b.dataset.cik.padStart(10, "0"),
          matched_title: b.dataset.name,
          note: "Picked from EDGAR's company list on the fundraising card.",
        });
        route();
      } catch (e) {
        b.disabled = false;
        b.textContent = "Try again";
        out.insertAdjacentHTML("afterbegin", `<p class="error">${esc(e.message || String(e))}</p>`);
      }
    };
  });
  wirePicks(card);      // an SEC record picked earlier under the company's name
  const search = async () => {
    const q = input.value.trim();
    if (q.length < 2) return;
    out.textContent = "Searching EDGAR…";
    try {
      const d = await api(`/v1/edgar/companies?q=${encodeURIComponent(q)}`);
      out.innerHTML = d.detail ? `<p class="muted">${esc(d.detail)}</p>` : formdResults(d.results);
    } catch (e) {
      out.textContent = e.message || String(e);
      return;
    }
    wirePicks(out);
  };
  card.querySelector("#fd-search").onclick = search;
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } };
}

/* Which SEC registrant this contractor is. Shown prominently because EDGAR
 * full-text search is constrained by CIK: a wrong mapping does not come back
 * empty, it comes back with another company's filings attached to this name. */
function identityCard(link, key) {
  if (!link) {
    return `<div class="card"><h3>SEC identity</h3>
      <div class="muted">Not resolved yet — screen this contractor to attempt a match.</div>
    </div>`;
  }
  const pct = link.confidence ? Math.round(link.confidence * 100) : null;
  const state = {
    confirmed: '<span class="pill warn">confirmed by a reviewer</span>',
    rejected: '<span class="pill warn">rejected — filings are not attributed</span>',
    auto: '<span class="pill">matched by name similarity, unreviewed</span>',
  }[link.status] || "";

  return `<div class="card" id="identity">
    <h3>SEC identity</h3>
    <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
      ${link.cik
        ? `<strong class="mono">CIK ${esc(link.cik)}</strong>
           <span>${esc(link.matched_title || "")}</span>`
        : "<strong>No registrant attributed</strong>"}
      ${state}
      ${pct !== null && link.status === "auto"
        ? `<span class="muted">name match ${pct}%</span>` : ""}
    </div>
    ${link.note ? `<div class="muted" style="margin-top:6px">${esc(link.note)}</div>` : ""}
    ${link.decided_by ? `<div class="muted" style="font-size:12px;margin-top:4px">
      decided by ${esc(link.decided_by)}</div>` : ""}
    <div class="muted" style="font-size:12px;margin-top:10px">
      Getting this wrong attributes another company's SEC filings to this one. A
      decision here is remembered and overrides the matcher on every later run.</div>
    <div class="actions" id="identity-actions">
      ${link.status !== "confirmed" && link.cik
        ? '<button class="primary id-btn" data-status="confirmed">Correct registrant</button>'
        : ""}
      ${link.status !== "rejected"
        ? '<button class="id-btn" data-status="rejected">Not this company</button>'
        : ""}
      <button class="ghost id-btn" data-status="override">Set a different CIK</button>
    </div>
    <div class="decide-panel" id="identity-panel" hidden>
      <label class="decide-prompt" for="cik-${esc(key)}">
        Ten-digit CIK, as EDGAR writes it (zero-padded).</label>
      <input id="cik-${esc(key)}" class="decide-note" inputmode="numeric"
             placeholder="0000936468">
      <label class="decide-prompt" style="margin-top:8px">Why? (recorded)</label>
      <textarea class="decide-note id-note" rows="2"></textarea>
      <div class="panel-error error" hidden></div>
      <div class="actions">
        <button class="primary id-save">Save</button>
        <button class="ghost id-cancel">Cancel</button>
      </div>
    </div>
  </div>`;
}

function wireIdentity(root, key) {
  const panel = root.querySelector("#identity-panel");
  if (!panel) return;
  const errorBox = panel.querySelector(".panel-error");
  const cikBox = panel.querySelector("input");
  const noteBox = panel.querySelector(".id-note");

  const send = async (status, cik) => {
    errorBox.hidden = true;
    const payload = { status, note: noteBox ? noteBox.value : "" };
    if (cik) payload.cik = cik;
    try {
      await apiPost(`/v1/entities/${encodeURIComponent(key)}/identity`, payload);
      route();
    } catch (e) {
      errorBox.innerHTML = `<strong>${esc(e.title)}</strong>
        <div class="muted" style="margin-top:4px">${esc(e.message)}</div>`;
      errorBox.hidden = false;
      panel.hidden = false;
    }
  };

  root.querySelectorAll(".id-btn").forEach((btn) => {
    btn.onclick = () => {
      if (btn.dataset.status === "override") {
        panel.hidden = false;
        cikBox.focus();
        return;
      }
      send(btn.dataset.status, null);
    };
  });
  const save = panel.querySelector(".id-save");
  if (save) save.onclick = () => send("confirmed", cikBox.value.trim());
  const cancel = panel.querySelector(".id-cancel");
  if (cancel) cancel.onclick = () => { panel.hidden = true; };
}

async function viewRules() {
  setBusy("Loading rules…");
  const [d, catalogue] = await Promise.all([
    api("/v1/rules/precision"),
    api("/v1/rules"),
  ]);
  const t = d.totals;

  const rows = catalogue.rules
    .map((r) => {
      const judged = r.true_positive + r.false_positive;
      const pct = r.precision === null ? null : Math.round(r.precision * 100);
      const colour = pct === null ? "var(--info)"
        : pct >= 80 ? "var(--low)" : pct >= 50 ? "var(--medium)" : "var(--critical)";
      return `<tr data-rule="${esc(r.rule_id)}" ${r.enabled ? "" : 'class="rule-off"'}>
        <td class="mono">${esc(r.rule_id)}
          ${r.overridden ? '<span class="pill">tuned</span>' : ""}</td>
        <td>${esc(CATEGORY_LABEL[r.category] || r.category || "—")}</td>
        <td class="num">${esc(num(r.true_positive))}</td>
        <td class="num">${esc(num(r.false_positive))}</td>
        <td class="num">${esc(num(r.unclear))}</td>
        <td class="num">${pct === null
          ? '<span class="muted">not measured</span>'
          : `<strong style="color:${colour}">${pct}%</strong>
             <span class="muted">of ${judged}</span>`}</td>
        <td class="num"><input class="rule-weight" type="number" min="0" max="5"
             step="0.1" value="${esc(r.weight)}" aria-label="weight"></td>
        <td>
          <button class="rule-toggle ${r.enabled ? "" : "primary"}">
            ${r.enabled ? "Disable" : "Enable"}</button>
          ${r.overridden ? '<button class="ghost rule-reset">Reset</button>' : ""}
        </td>
      </tr>`;
    })
    .join("");

  view.innerHTML = `
    <div class="page-head">
      <h1>Rule precision</h1>
      <div class="sub">What reviewers concluded, worst first. Weights in this tool
        were set by judgement; this is the only thing that measures them.</div>
    </div>

    <div class="grid cols-3">
      ${statCard("Verdicts recorded", num(t.verdicts))}
      ${statCard("Rules with verdicts", num(t.rules_with_verdicts))}
      ${statCard("Overall precision",
                 t.overall_precision === null ? "—"
                   : Math.round(t.overall_precision * 100) + "%")}
    </div>

    ${catalogue.rules.length ? `<div class="card">
      <div class="table-wrap"><table>
        <thead><tr><th>Rule</th><th>Category</th><th class="num">Confirmed</th>
        <th class="num">False positive</th><th class="num">Unclear</th>
        <th class="num">Precision</th><th class="num">Weight</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <div class="muted" style="font-size:12px;margin-top:12px">
        "Unclear" is counted but kept out of the precision denominator: a reviewer
        who could not tell has not said the rule was wrong, and folding that in
        would punish rules that raise genuinely hard questions. A rule with no
        verdicts reads "not measured" rather than 100% — unmeasured is not the
        same as perfect.<br>
        Weight scales a rule's score and the severity band is recomputed from the
        result, so a rule scored down cannot keep a label it no longer earns.
        Changes apply to the next screen, not to findings already recorded.</div>
    </div>` : `<div class="card"><div class="empty">
      No rules have fired here yet. Run a screen, then mark its signals
      confirmed or false positive; they collect here.</div></div>`}`;

  view.querySelectorAll("tr[data-rule]").forEach((row) => {
    const ruleId = row.dataset.rule;
    const weightBox = row.querySelector(".rule-weight");

    const save = async (enabled, weight) => {
      try {
        await apiSend("PUT", `/v1/rules/${encodeURIComponent(ruleId)}`,
                      { enabled, weight: Number(weight) });
        route();
      } catch (e) {
        showError(e);
      }
    };

    const isOff = () => row.classList.contains("rule-off");
    const toggle = row.querySelector(".rule-toggle");
    if (toggle) {
      toggle.onclick = () => save(isOff(), weightBox ? weightBox.value : 1);
    }
    if (weightBox) {
      weightBox.onchange = () => save(!isOff(), weightBox.value);
    }
    const reset = row.querySelector(".rule-reset");
    if (reset) {
      reset.onclick = async () => {
        try {
          await apiSend("DELETE", `/v1/rules/${encodeURIComponent(ruleId)}`);
          route();
        } catch (e) {
          showError(e);
        }
      };
    }
  });
}

function documentsTable(docs, entityKey) {
  if (!docs.length) {
    return `<div class="empty">No documents recorded. Re-run a screen to index them.</div>`;
  }
  const rows = docs
    .map((doc) => {
      const diffable = doc.revisions > 1;
      const target = `#/diff/${encodeURIComponent(doc.source)}/`
        + `${encodeURIComponent(doc.document_key)}?entity=${encodeURIComponent(entityKey)}`;
      return `<tr>
        <td>${doc.url
              ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer">${esc(doc.title || doc.document_key)}</a>`
              : esc(doc.title || doc.document_key)}
          <div class="muted mono" style="font-size:11.5px">${esc(doc.document_key)}</div></td>
        <td><span class="pill">${esc(doc.source)}</span></td>
        <td class="num">${esc(num(doc.revisions))}</td>
        <td class="muted">${esc(day(doc.last_seen_at))}</td>
        <td>${diffable
              ? `<a href="${target}">View changes</a>`
              : '<span class="muted">single revision</span>'}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Document</th><th>Source</th><th class="num">Revisions</th>
      <th>Last seen</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

async function viewDiff(source, key, fromSha, entityKey) {
  setBusy("Loading changes…");
  const qs = `source=${encodeURIComponent(source)}&key=${encodeURIComponent(key)}`;
  let diffQs = fromSha ? `${qs}&from_sha=${encodeURIComponent(fromSha)}` : qs;
  if (entityKey) diffQs += `&entity_key=${encodeURIComponent(entityKey)}`;

  const [history, diff] = await Promise.all([
    api(`/v1/documents/history?${qs}`),
    api(`/v1/documents/diff?${diffQs}`),
  ]);

  const rendered = diff.lines
    .map((l) => {
      const rules = l.rules || [];
      const cls = `dl-${esc(l.kind)}${rules.length ? " dl-matched" : ""}`;
      const title = rules.length ? ` title="Matched by ${esc(rules.join(", "))}"` : "";
      const badge = rules.length
        ? `<span class="rule-tag">${esc(rules.join(" "))}</span>`
        : "";
      return `<div class="${cls}"${title}>${badge}${esc(l.text) || "&nbsp;"}</div>`;
    })
    .join("");

  const matchedCount = diff.lines.filter((l) => (l.rules || []).length).length;

  const revisionRows = history.revisions
    .map(
      (r) => `<tr>
        <td class="mono">${esc(r.sha256.slice(0, 12))}</td>
        <td>${esc(r.observed_at)}</td>
        <td>${r.has_body
              ? (r.sha256 === (diff.to || {}).sha256
                  ? '<span class="pill warn">shown as “after”</span>'
                  : r.sha256 === (diff.from || {}).sha256
                    ? '<span class="pill warn">shown as “before”</span>'
                    : `<a href="#/diff/${encodeURIComponent(source)}/${encodeURIComponent(key)}/${encodeURIComponent(r.sha256)}">compare to latest</a>`)
              : '<span class="muted">text no longer retained</span>'}</td>
      </tr>`
    )
    .join("");

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Document changes</div>
    <div class="page-head">
      <h1>${esc(key)}</h1>
      <div class="sub">
        <span class="pill">${esc(source)}</span>
        ${diff.baseline
          ? '<span class="pill warn">first observation</span>'
          : `<span class="pill">+${diff.stats.added} / −${diff.stats.removed} lines</span>`}
      </div>
    </div>

    ${diff.baseline ? `<div class="card"><div class="muted">
      This is the first time the document was recorded, so all of it reads as new.
      That is a baseline, not a change the contractor made.</div></div>` : ""}

    ${(diff.signals || []).length ? `<div class="card">
      <h2>Signals from this document</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        ${matchedCount
          ? `${matchedCount} line(s) below are marked with the rule that fired on them.`
          : "None of these rules matched a changed line — they fired on text that "
            + "was already there, which is a weaker basis than a fresh disclosure."}</div>
      ${signalList(diff.signals)}
    </div>` : ""}

    <div class="card">
      <h2>${diff.baseline ? "Content" : "What changed"}</h2>
      ${diff.from ? `<div class="muted" style="font-size:12px;margin-bottom:10px">
        <span class="mono">${esc(diff.from.sha256.slice(0, 12))}</span>
        (${esc(diff.from.observed_at)}) →
        <span class="mono">${esc(diff.to.sha256.slice(0, 12))}</span>
        (${esc(diff.to.observed_at)})</div>` : ""}
      <div class="difflines">${rendered || '<div class="empty">No textual difference.</div>'}</div>
    </div>

    <div class="card">
      <h2>Revisions</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Hash</th><th>Observed</th><th></th></tr></thead>
        <tbody>${revisionRows}</tbody></table></div>
    </div>`;
}

async function viewOfficer(email) {
  setBusy("Loading contracting officer…");
  const d = await api(`/v1/officers/${encodeURIComponent(email)}`);
  const o = d.officer;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Contracting officer</div>
    <div class="page-head">
      <h1>${esc(o.name || o.email)}</h1>
      <div class="sub mono">${esc(o.email)}</div>
      <div class="sub" style="margin-top:6px">
        <span class="pill">${esc(o.confidence || "unknown")} confidence</span>
        <span class="pill">${esc(o.source || "source unrecorded")}</span>
        ${o.agency ? `<span class="pill">${esc(o.agency)}</span>` : ""}
      </div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Awards", num(d.contract_count ?? d.contracts.length))}
      ${statCard("Flagged contractors", num(d.findings.length))}
    </div>

    <div class="card">
      <h2>Contractors with findings</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        Notices about these would be addressed to this officer.</div>
      ${d.findings.length
        ? entitiesTable(d.findings.map((f) => ({
            entity_key: (f.entity || {}).uei || (f.entity || {}).name,
            entity_name: (f.entity || {}).name,
            severity: f.severity,
            contract_count: (f.contracts || []).length,
            obligated: (f.contracts || []).reduce((a, c) => a + (c.award_amount || 0), 0),
            last_screened: f.generated_at,
          })))
        : '<div class="empty">No findings on this officer’s contractors.</div>'}
    </div>

    ${addToPortfolioButton(
        [...new Set(d.contracts.map((c) => c.entity_key).filter(Boolean))],
        "Watch the contractors this officer holds awards with.")}

    <div class="card">
      <h2>Awards</h2>
      ${truncationNote(d.contracts_shown, d.contract_count)}
      ${contractsTable(d.contracts)}
    </div>`;

  wireAddToPortfolio();
}

async function viewAgency(name) {
  setBusy("Loading agency…");
  const d = await api(`/v1/agencies/${encodeURIComponent(name)}`);

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Agency</div>
    <div class="page-head">
      <h1>${esc(d.agency)}</h1>
      <div class="sub">${num(d.contract_count)} award(s) screened</div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Contractors", num(d.entity_count ?? d.entities.length))}
      ${statCard("Officers", num(d.officers.length))}
    </div>

    <div class="card">
      <h2>Contractors by obligated value</h2>
      ${barChart(d.entities.slice(0, 12), {
        label: "entity_name",
        value: "obligated",
        href: (r) => `#/entity/${encodeURIComponent(r.entity_key)}`,
        color: (r) => (r.severity ? `var(--${r.severity})` : "var(--info)"),
      })}
    </div>

    <div class="card"><h2>Contractors</h2>
      ${entitiesTable(d.entities, { showScreened: false })}</div>

    <div class="card"><h2>Contracting officers</h2>${officersTable(d.officers)}</div>

    ${addToPortfolioButton(
        d.entities.map((e) => e.entity_key).filter(Boolean),
        "Watch this agency's contractors on your dashboard.")}`;

  wireAddToPortfolio();
}

async function viewContract(key) {
  setBusy("Loading award…");
  const d = await api(`/v1/contracts/${encodeURIComponent(key)}`);
  const c = d.contract;

  const field = (label, value) =>
    `<tr><th style="width:210px">${esc(label)}</th><td>${value}</td></tr>`;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Award</div>
    <div class="page-head">
      <h1 class="mono">${esc(c.piid || c.contract_key)}</h1>
      <div class="sub">${linkEntity(c.entity_key, c.entity_name)} · ${esc(money(c.amount))}</div>
    </div>

    <div class="card">
      <h2>Award</h2>
      <div class="table-wrap"><table>
        ${field("Contractor", linkEntity(c.entity_key, c.entity_name))}
        ${field("Agency", esc(c.agency || "—"))}
        ${field("Sub-agency", esc(c.sub_agency || "—"))}
        ${field("Obligated", esc(money(c.amount)))}
        ${field("Period", `${esc(day(c.start_date))} → ${esc(day(c.end_date))}`)}
        ${field("Requirement", esc(c.psc_description || c.naics_description || "—"))}
        ${field("Description", esc(c.description || "—"))}
        ${field("Solicitation", `<span class="mono">${esc(c.solicitation_id || "—")}</span>`)}
        ${field("UEI", `<span class="mono">${esc(c.recipient_uei || "—")}</span>`)}
        ${field("Country of incorporation", esc(c.country_of_incorporation || "—"))}
        ${field("Foreign owned / located", c.foreign_owned
            ? '<span class="pill warn">yes</span>' : "no")}
        ${field("Foreign funding", esc(c.foreign_funding || "—"))}
      </table></div>
    </div>

    <div class="card">
      <h2>Contracting officer</h2>
      ${c.ko_email
        ? `<div><a href="#/officer/${encodeURIComponent(c.ko_email)}">${esc(c.ko_name || c.ko_email)}</a>
           <div class="muted mono">${esc(c.ko_email)}</div>
           <div style="margin-top:8px">
             <span class="pill">${esc(c.ko_confidence || "unknown")} confidence</span>
             <span class="pill">${esc(c.ko_source || "source unrecorded")}</span>
           </div></div>`
        : '<div class="empty">No contracting officer resolved for this award. A notice about it could not be addressed automatically.</div>'}
    </div>

    ${(c.ip_clauses || []).length ? `<div class="card">
      <h2>Data-rights clauses</h2>
      <div class="muted" style="font-size:12px;margin-bottom:8px">
        These decide what the Government keeps if the contractor's IP moves.</div>
      ${c.ip_clauses.map((x) => `<span class="pill warn">${esc(x)}</span>`).join("")}
    </div>` : ""}

    ${d.entity_finding ? `<div class="card">
      <h2>Contractor signals</h2>
      ${signalList(d.entity_finding.signals)}
    </div>` : ""}

    ${c.source_url ? `<div class="card"><a href="${esc(c.source_url)}" target="_blank"
      rel="noopener noreferrer">Open source record →</a></div>` : ""}`;
}

async function viewNotices() {
  setBusy("Loading notices…");
  const d = await api("/v1/notices?limit=100");

  if (!d.notices.length) {
    view.innerHTML = `<div class="page-head"><h1>Notices</h1></div>
      <div class="card"><div class="empty">No notices yet. What raises one is set
      on the <a href="#/screening">Screening</a> page.</div></div>`;
    return;
  }

  const cards = d.notices
    .map(
      (n) => `<div class="card" data-notice="${esc(n.notice_id)}">
      <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        ${sevTag(n.severity)}
        <strong>${esc(n.entity_name)}</strong>
        <span class="pill">${esc(n.status)}</span>
        ${n.edits ? '<span class="pill warn">edited by reviewer</span>' : ""}
        <span class="muted" style="margin-left:auto">${esc(day(n.created_at))}</span>
      </div>
      <div style="margin:10px 0 6px">${esc(n.subject)}</div>
      ${n.trigger_reason ? `<div class="muted" style="font-size:12.5px;margin-bottom:4px">
        <strong>Why this was raised:</strong> ${esc(n.trigger_reason)}</div>` : ""}
      <div class="muted" style="font-size:12.5px">
        To: ${n.recipient ? `<span class="mono">${esc(n.recipient)}</span>
             <span class="pill">${esc(n.officer_confidence || "")}</span>`
            : "<em>no contracting officer resolved</em>"}
      </div>
      <details style="margin-top:10px">
        <summary class="muted" style="cursor:pointer">Read the notice</summary>
        <div class="notice-body">${esc(n.body_text)}</div>
      </details>
      ${n.edits ? `<details style="margin-top:6px">
        <summary class="muted" style="cursor:pointer">
          What the reviewer changed (+${n.edits.stats.added} / −${n.edits.stats.removed} lines)</summary>
        <div class="difflines" style="margin-top:8px">${plainDiff(n.edits.lines)}</div>
      </details>` : ""}
      <div class="actions">
        <button class="dl">Download .eml</button>
        ${n.status === "pending"
          ? `<button class="primary approve" ${n.recipient ? "" : "disabled title='No recipient resolved'"}>Approve</button>
             <button class="edit" ${n.recipient ? "" : "disabled title='No recipient resolved'"}>Edit, then approve</button>
             <button class="reject">Reject</button>`
          : `<span class="muted" style="align-self:center">
               decided by ${esc(n.decided_by || "—")}${n.decision_note ? ` · ${esc(n.decision_note)}` : ""}</span>`}
      </div>
      <div class="decide-panel" hidden>
        <div class="edit-area" hidden>
          <label class="decide-prompt" for="body-${esc(n.notice_id)}">
            Notice text. Reword anything; the limitations statement at the end has
            to stay, because it is what makes this a screen rather than an accusation.
            The generated version is kept alongside whatever you approve.</label>
          <textarea id="body-${esc(n.notice_id)}" class="decide-note edit-body mono"
                    rows="18">${esc(n.body_text)}</textarea>
        </div>
        <label class="decide-prompt note-prompt" for="note-${esc(n.notice_id)}"></label>
        <textarea id="note-${esc(n.notice_id)}" class="decide-note note-body" rows="3"></textarea>
        <div class="panel-error error" hidden></div>
        <div class="actions">
          <button class="primary confirm"></button>
          <button class="ghost cancel">Cancel</button>
        </div>
      </div>
    </div>`
    )
    .join("");

  const pending = d.notices.filter((n) => n.status === "pending").length;
  view.innerHTML = `
    <div class="page-head">
      <h1>Notices</h1>
      <div class="sub">${pending} awaiting review. Approving records a decision —
        it does not transmit anything unless Gmail delivery is armed on the server.</div>
    </div>${cards}`;

  const PROMPTS = {
    approve: {
      label: "What did you verify? (optional, recorded with the approval)",
      confirm: "Confirm approval",
    },
    edit: {
      label: "What did you change, and why? (recorded with the approval)",
      confirm: "Approve edited notice",
    },
    reject: {
      label: "Why is this a false positive? Rejections are the only labelled data " +
             "rule tuning ever gets, so a sentence here is worth more than it looks.",
      confirm: "Confirm rejection",
    },
  };

  const byId = Object.fromEntries(d.notices.map((n) => [n.notice_id, n]));

  view.querySelectorAll("[data-notice]").forEach((card) => {
    const id = card.dataset.notice;
    const panel = card.querySelector(".decide-panel");
    const editArea = panel.querySelector(".edit-area");
    const bodyBox = panel.querySelector(".edit-body");
    const noteBox = panel.querySelector(".note-body");
    const errorBox = panel.querySelector(".panel-error");
    const dl = card.querySelector(".dl");
    if (dl) dl.onclick = () => downloadNotice(id);

    const open = (mode) => {
      panel.querySelector(".note-prompt").textContent = PROMPTS[mode].label;
      const confirm = panel.querySelector(".confirm");
      confirm.textContent = PROMPTS[mode].confirm;
      editArea.hidden = mode !== "edit";
      errorBox.hidden = true;
      confirm.onclick = async () => {
        const action = mode === "reject" ? "reject" : "approve";
        const original = byId[id].body_text || "";
        // Unchanged text is not an edit, and must not be recorded as one.
        const bodyText = mode === "edit" && bodyBox.value.trim() !== original.trim()
          ? bodyBox.value : "";
        confirm.disabled = true;
        const failure = await decide(id, action, noteBox.value, bodyText);
        confirm.disabled = false;
        if (failure) {
          // Shown in place: replacing the page would discard a long edit over
          // one validation message.
          errorBox.innerHTML = `<strong>${esc(failure.title)}</strong>
            <div class="muted" style="margin-top:4px">${esc(failure.message)}</div>`;
          errorBox.hidden = false;
        }
      };
      panel.hidden = false;
      (mode === "edit" ? bodyBox : noteBox).focus();
    };

    const ap = card.querySelector(".approve");
    if (ap) ap.onclick = () => open("approve");
    const ed = card.querySelector(".edit");
    if (ed) ed.onclick = () => open("edit");
    const rj = card.querySelector(".reject");
    if (rj) rj.onclick = () => open("reject");
    const cancel = card.querySelector(".cancel");
    if (cancel) cancel.onclick = () => { panel.hidden = true; };
  });
}

function plainDiff(lines) {
  return lines
    .map((l) => `<div class="dl-${esc(l.kind)}">${esc(l.text) || "&nbsp;"}</div>`)
    .join("");
}

/* Returns an error object on failure rather than rendering it, so the caller
 * can show it next to whatever the reviewer was typing. */
async function decide(id, action, note, bodyText) {
  const payload = { note: note || "" };
  if (bodyText) payload.body_text = bodyText;
  try {
    await apiPost(`/v1/notices/${encodeURIComponent(id)}/${action}`, payload);
    route();
    return null;
  } catch (e) {
    return e;
  }
}

async function downloadNotice(id) {
  // Needs the auth header, so a plain link will not do.
  const res = await fetch(`/v1/notices/${encodeURIComponent(id)}.eml`, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
  });
  if (!res.ok) {
    alert("Could not download the notice.");
    return;
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `notice_${id}.eml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* --------------------------------------------------------------- portfolio */

/* The key is the portfolio. It is kept here only so the page reopens without
 * being re-pasted; the copy that matters is the one saved in a file, because
 * browser storage is per-browser, is cleared by the things that clear browser
 * storage, and is gone on the next machine. The UI says so rather than
 * letting someone discover it. */
const PORTFOLIO_STORAGE = "foci.portfolio";

function savedPortfolioKey() {
  try {
    return localStorage.getItem(PORTFOLIO_STORAGE) || "";
  } catch {
    return "";
  }
}

function rememberPortfolioKey(key) {
  try {
    if (key) localStorage.setItem(PORTFOLIO_STORAGE, key);
    else localStorage.removeItem(PORTFOLIO_STORAGE);
  } catch { /* private mode: the file is the copy that counts anyway */ }
}

/* Adding to the portfolio is a button on the page for the thing being added,
 * never a side effect of looking at it. Searching for a company, or reading
 * its page, does not start watching it — that is a decision, and it is made
 * here. */
function addToPortfolioButton(keys, label) {
  if (!keys.length) return "";
  const many = keys.length > 1;
  return `
    <div class="actions">
      <button id="pf-add" data-keys="${esc(keys.join("|"))}">
        ${many ? `Add ${num(keys.length)} contractors to portfolio` : "Add to portfolio"}
      </button>
      <span class="muted" id="pf-add-status" style="font-size:12.5px;align-self:center">
        ${esc(label || "")}</span>
    </div>`;
}

function wireAddToPortfolio() {
  const button = document.getElementById("pf-add");
  if (!button) return;
  const status = document.getElementById("pf-add-status");
  button.onclick = async () => {
    const keys = button.dataset.keys.split("|").filter(Boolean);
    button.disabled = true;
    try {
      const d = await apiPost("/v1/portfolio/edit", {
        key: savedPortfolioKey(), add: keys,
      });
      rememberPortfolioKey(d.key);
      await syncAlertList(d.key);
      status.innerHTML = `Added. Portfolio now holds ${num(d.companies)}
        compan${d.companies === 1 ? "y" : "ies"} —
        <a href="#/portfolio">open it</a>. Save the key to a file from there.`;
      button.textContent = "Added";
    } catch (e) {
      button.disabled = false;
      status.textContent = e.message || String(e);
    }
  };
}

function portfolioTable(companies) {
  const contractors = companies.filter((r) => r.kind !== "adviser");
  const firms = companies.filter((r) => r.kind === "adviser");

  // What the contractor last told the SEC it raised privately (Form D). Blank
  // until someone picks its SEC record on its page — see formdPicker.
  const raise = (r) => {
    if (!r.cik) return '<span class="muted" title="Pick its SEC record on the contractor page">—</span>';
    if (!r.formd_checked) return '<span class="muted">not checked yet</span>';
    if (!r.latest_raise) return '<span class="muted">none filed</span>';
    const x = r.latest_raise;
    return `${x.amount_sold === null || x.amount_sold === undefined ? "—" : money(x.amount_sold)}
      <span class="muted" style="font-size:12px">· ${esc(x.filed)}</span>`;
  };

  const contractorRows = contractors.map((r) => `
    <tr>
      <td>${linkEntity(r.page_key || r.entity_key, r.entity_name)}</td>
      <td>${r.screened_here ? sevTag(r.severity) : '<span class="pill">not screened here</span>'}</td>
      <td class="num">${r.contract_count ? num(r.contract_count) : "—"}</td>
      <td class="num">${r.obligated ? money(r.obligated) : "—"}</td>
      <td>${raise(r)}</td>
      <td>${r.last_screened ? day(r.last_screened) : "—"}</td>
    </tr>`).join("");

  const firmRows = firms.map((r) => `
    <tr>
      <td><a href="#/adviser/${encodeURIComponent(r.crd)}">${esc(r.entity_name)}</a></td>
      <td>${r.adv_filing_date ? esc(r.adv_filing_date) : '<span class="pill">not read yet</span>'}</td>
      <td class="num">${r.screened_here ? num(r.fund_count) : "—"}</td>
      <td class="num">${r.fund_assets ? money(r.fund_assets) : "—"}</td>
      <td class="num">${r.foreign_owners === null || r.foreign_owners === undefined ? "—"
        : r.foreign_owners ? `<span class="pill warn">${num(r.foreign_owners)}</span>` : "none"}</td>
    </tr>`).join("");

  return `
    ${contractors.length ? `<table>
      <thead><tr><th>Contractor</th><th>Latest severity</th><th class="num">Awards</th>
        <th class="num">Obligated</th><th title="From the company's latest SEC Form D">Latest private raise</th>
        <th>Screened</th></tr></thead>
      <tbody>${contractorRows}</tbody></table>` : ""}
    ${firms.length ? `<h3 style="margin-top:${contractors.length ? "18px" : "0"}">Investment firms</h3>
      <table>
      <thead><tr><th>Firm</th><th>Latest Form ADV</th><th class="num">Private funds</th>
        <th class="num">Money in those funds</th>
        <th class="num" title="Owners of the firm that its Form ADV marks as companies based outside the United States">Owners outside the U.S.</th></tr></thead>
      <tbody>${firmRows}</tbody></table>` : ""}`;
}

/* ------------------------------------------------------ investment firms */

/* Form ADV is filed by investment firms — private equity, venture capital,
 * hedge fund managers — not by contractors. The firms that own or back a
 * contractor are the ones whose filings say when money is raised and how much
 * of it comes from outside the U.S., so they are added to a portfolio by name
 * and watched alongside the contractors. */
function advisersCard() {
  return `
    <div class="card">
      <h2>Watch an investment firm</h2>
      <p class="muted">Private equity, venture capital and other investment firms
      file a public form with the SEC — <strong>Form ADV</strong> — listing the funds
      they run, how much money is in each, how many investors, and how much of each
      is owned by investors outside the United States. Add the firms that own or back
      your contractors, and you will be told when they raise a new fund or when that
      foreign share changes. Contractors themselves do not file this form.</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input id="adv-q" placeholder="Firm name, e.g. Arlington Capital"
          style="flex:1;min-width:220px;padding:8px;border:1px solid var(--border);
          border-radius:8px;background:var(--surface-2);color:var(--text)">
        <button id="adv-search">Search</button>
      </div>
      <div id="adv-results" style="margin-top:10px"></div>
    </div>`;
}

function wireAdvisers() {
  const button = document.getElementById("adv-search");
  if (!button) return;
  const input = document.getElementById("adv-q");
  const out = document.getElementById("adv-results");
  const search = async () => {
    const q = input.value.trim();
    if (q.length < 2) return;
    out.textContent = "Searching the SEC's adviser database…";
    try {
      const d = await api(`/v1/advisers/search?q=${encodeURIComponent(q)}`);
      if (!d.firms.length) {
        out.textContent = d.detail || "No registered investment firm by that name.";
        return;
      }
      out.innerHTML = `<table><tbody>${d.firms.map((f) => `
        <tr><td><strong>${esc(f.name)}</strong>
          <span class="muted" style="font-size:12px"> · CRD ${esc(f.crd)}
          ${f.status ? " · " + esc(f.status.toLowerCase()) : ""}</span></td>
        <td style="text-align:right">
          <button class="adv-add" data-crd="${esc(f.crd)}" data-name="${esc(f.name)}">Watch</button>
        </td></tr>`).join("")}</tbody></table>`;
      out.querySelectorAll(".adv-add").forEach((b) => {
        b.onclick = async () => {
          b.disabled = true;
          b.textContent = "Adding…";
          const key = `CRD:${b.dataset.crd}`;
          try {
            const r = await apiPost("/v1/portfolio/edit", {
              key: savedPortfolioKey(), add: [key], names: { [key]: b.dataset.name },
            });
            rememberPortfolioKey(r.key);
            await syncAlertList(r.key);
            viewPortfolio();
          } catch (e) {
            b.disabled = false;
            b.textContent = "Watch";
            out.insertAdjacentHTML("beforeend",
              `<p class="muted">${esc(e.message || String(e))}</p>`);
          }
        };
      });
    } catch (e) {
      out.textContent = e.message || String(e);
    }
  };
  button.onclick = search;
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } };
}

/* ------------------------------------------------------------ email alerts */

const ALERT_LIST_STORAGE = "foci.alertlist";

function savedAlertListId() {
  try { return localStorage.getItem(ALERT_LIST_STORAGE) || ""; } catch { return ""; }
}

function rememberAlertListId(id) {
  try {
    if (id) localStorage.setItem(ALERT_LIST_STORAGE, id);
    else localStorage.removeItem(ALERT_LIST_STORAGE);
  } catch { /* the list still exists on the server */ }
}

/* When the portfolio changes, the alert list follows it — otherwise a firm
 * added today would quietly never be watched. */
async function syncAlertList(newKey) {
  const id = savedAlertListId();
  if (!id || !newKey) return;
  try {
    const d = await api("/v1/alerts");
    const list = d.subscriptions.find((s) => s.subscription_id === id);
    if (!list) return;
    await apiPost("/v1/alerts/subscriptions", {
      subscription_id: id, name: list.name, emails: list.emails,
      portfolio_key: newKey, active: list.active,
    });
  } catch { /* shown as out of date on the portfolio page instead */ }
}

function deliveryRows(deliveries) {
  if (!deliveries.length) {
    return '<p class="muted" style="font-size:12.5px">No alerts yet. The first one is sent when something changes after the list is saved.</p>';
  }
  const label = { sent: "sent", drafted: "written, not sent", failed: "failed" };
  return deliveries.map((d) => `
    <details style="margin:6px 0">
      <summary style="cursor:pointer;font-size:13px">
        <span class="pill${d.status === "failed" ? " warn" : ""}">${esc(label[d.status] || d.status)}</span>
        ${esc(d.subject)} <span class="muted">· ${esc(day(d.created_at))}
        · to ${esc(d.recipients.join(", "))}</span></summary>
      ${d.detail ? `<p class="muted" style="font-size:12px">${esc(d.detail)}</p>` : ""}
      <div class="notice-body" style="font-size:12.5px">${esc(d.body_text)}</div>
    </details>`).join("");
}

async function alertsCard(key, portfolioName) {
  let d;
  try {
    d = await api("/v1/alerts");
  } catch (e) {
    return `<div class="card"><h2>Email alerts</h2><p class="muted">${esc(e.message || String(e))}</p></div>`;
  }
  const id = savedAlertListId();
  const list = d.subscriptions.find((s) => s.subscription_id === id);
  const deliveries = list ? d.deliveries.filter((x) => x.subscription_id === list.subscription_id) : [];
  const outOfDate = list && list.portfolio_key !== key;
  const mode = d.sending.mode;

  return `
    <div class="card" id="alerts-card">
      <h2>Email alerts</h2>
      <p class="muted">Anyone on this list gets an email when something changes for a
      company in this portfolio — a new fund or a change in foreign ownership at an
      investment firm, a contractor filing a notice that it raised money (Form D), or a
      contractor being flagged. Each email says <strong>what changed and where to
      look</strong>. It does not judge what the change means; that is left to the person
      reading it.</p>
      <p class="muted" style="font-size:12.5px">Fundraising notices are watched only for
      contractors whose SEC record has been picked — on the contractor's page, under
      "${FORMD_TITLE}".</p>

      <div class="${mode === "send" ? "muted" : "error"}" style="font-size:12.5px;margin:8px 0">
        ${esc(d.sending.explanation)}</div>

      <label class="field" style="display:block">Send to (one or more addresses)
        <textarea id="alert-emails" rows="2" style="width:100%;margin-top:4px"
          placeholder="name@agency.gov, colleague@example.com">${esc(list ? list.emails.join(", ") : "")}</textarea>
      </label>
      ${outOfDate ? `<p class="muted" style="font-size:12.5px">This portfolio has changed
        since the list was saved. Save again to watch the new companies.</p>` : ""}
      <div class="actions">
        <button class="primary" id="alert-save">${list ? "Update list" : "Start email alerts"}</button>
        ${list ? `<button id="alert-run">Check now</button>
                  <button id="alert-test">Send a test email</button>
                  <button class="ghost" id="alert-delete">Stop alerts</button>` : ""}
      </div>
      <p class="muted" id="alert-status" style="font-size:12.5px;margin-top:8px">
        ${list && list.last_run_at ? `Last checked ${esc(day(list.last_run_at))}.` : ""}</p>

      ${list ? `<h3 style="margin-top:14px">Recent alerts</h3>${deliveryRows(deliveries)}` : ""}
    </div>`;
}

function wireAlerts(key, portfolioName) {
  const status = document.getElementById("alert-status");
  const save = document.getElementById("alert-save");
  if (!save) return;
  save.onclick = async () => {
    save.disabled = true;
    try {
      const r = await apiPost("/v1/alerts/subscriptions", {
        subscription_id: savedAlertListId(),
        name: portfolioName || "Portfolio",
        emails: [document.getElementById("alert-emails").value],
        portfolio_key: key,
      });
      rememberAlertListId(r.subscription.subscription_id);
      viewPortfolio();
    } catch (e) {
      save.disabled = false;
      status.textContent = e.message || String(e);
    }
  };

  const run = document.getElementById("alert-run");
  if (run) {
    run.onclick = async () => {
      run.disabled = true;
      status.textContent = "Checking every firm and contractor in the portfolio…";
      try {
        const r = await apiPost("/v1/alerts/run", {});
        const mine = (r.emails || []).find((e) => e.subscription === portfolioName) || {};
        const checked = `Checked ${r.firms_checked} investment firm(s) and `
          + `${r.companies_checked || 0} contractor(s) with an SEC record.`;
        status.textContent = mine.status === "nothing new"
          ? `${checked} Nothing new since the last alert.`
          : `${checked} ${mine.items || 0} update(s): ${mine.status}.`;
        setTimeout(viewPortfolio, 1500);
      } catch (e) {
        run.disabled = false;
        status.textContent = e.message || String(e);
      }
    };
    document.getElementById("alert-test").onclick = async () => {
      status.textContent = "Sending a test…";
      try {
        const r = await apiPost(`/v1/alerts/subscriptions/${encodeURIComponent(savedAlertListId())}/test`, {});
        status.textContent = r.status === "sent"
          ? `Test sent to ${r.recipients.join(", ")}.`
          : `Test ${r.status}: ${r.detail}`;
        setTimeout(viewPortfolio, 1500);
      } catch (e) {
        status.textContent = e.message || String(e);
      }
    };
    document.getElementById("alert-delete").onclick = async () => {
      await apiSend("DELETE", `/v1/alerts/subscriptions/${encodeURIComponent(savedAlertListId())}`);
      rememberAlertListId("");
      viewPortfolio();
    };
  }
}

/* ------------------------------------------------------------ one firm */

/* Who owns the investment firm itself: Schedule A (direct owners and top
 * executives) and Schedule B (who owns those owners). The form marks each
 * owner as a person, a U.S. company or a foreign company and stops there — no
 * country, and nothing about a person's nationality — so the page says exactly
 * that much and no more. */
function ownersCard(s, links) {
  const title = "Who owns and runs this firm";
  if (!s.owners_read) {
    return `<div class="card" id="owners-card"><h2>${title}</h2>
      <p class="muted">The ownership pages of this filing could not be read here. They
      are Schedule A and Schedule B of the full form:
      <a class="linkish" href="${esc(links.form)}" target="_blank" rel="noopener noreferrer">open it →</a></p></div>`;
  }
  const owners = s.owners || [];
  const direct = owners.filter((o) => !o.indirect);
  const indirect = owners.filter((o) => o.indirect);
  const foreign = owners.filter((o) => o.foreign);
  const what = (o) => ({
    I: "Person", DE: "U.S. company",
    FE: '<span class="pill warn">company outside the U.S.</span>',
  }[o.kind] || "—");
  const yes = (v) => (v ? "Yes" : "No");
  const share = (o) => (o.code === "F" ? "General partner, trustee or manager"
    : o.code === "NA" && !o.indirect ? "Under 5%, or none" : esc(o.share || "—"));

  // People are filed "Last, First, Middle" and shown first-name-first; the
  // tooltip keeps the filed form, since not every filer follows the order.
  const name = (o) => `<span title="As filed: ${esc(o.name)}">${esc(o.display)}</span>`;

  return `<div class="card" id="owners-card">
    <h2>${title}</h2>
    <p class="muted">From the firm's own filing. The form marks each owner as a person, a
    U.S. company, or a company based outside the United States. It does not say which
    country, and it does not record the nationality of people. “Control” is the form's
    word for the power to direct the firm's management or policies.</p>
    <p>${foreign.length
      ? `<strong>${num(foreign.length)} of the ${num(owners.length)}</strong> names listed
         ${foreign.length === 1 ? "is a company" : "are companies"} based outside the United States:
         ${foreign.map((o) => esc(o.display)).join(", ")}.`
      : `None of the ${num(owners.length)} names listed is marked as a company based outside
         the United States.`}</p>

    <h3>Direct owners and top executives <span class="muted" style="font-weight:400">· Schedule A</span></h3>
    ${direct.length ? `<table>
      <thead><tr><th>Name</th><th>What it is</th><th>Role</th><th>Since</th>
        <th>Owns</th><th>Has control</th></tr></thead>
      <tbody>${direct.map((o) => `<tr>
        <td>${name(o)}</td><td>${what(o)}</td>
        <td>${esc((o.title || "—").toLowerCase())}</td><td>${esc(o.since || "—")}</td>
        <td>${share(o)}</td><td>${yes(o.control)}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted">None listed.</p>'}

    <h3 style="margin-top:16px">Who owns those owners <span class="muted" style="font-weight:400">· Schedule B</span></h3>
    ${indirect.length ? `<p class="muted" style="font-size:12.5px">Each row is one step up
      the chain: the name on the left owns the share shown of the name in the “Of” column.</p>
      <table>
      <thead><tr><th>Name</th><th>What it is</th><th>Owns</th><th>Of</th><th>Since</th>
        <th>Has control</th></tr></thead>
      <tbody>${indirect.map((o) => `<tr>
        <td>${name(o)}</td><td>${what(o)}</td><td>${share(o)}</td>
        <td>${esc(o.through || "—")}</td><td>${esc(o.since || "—")}</td>
        <td>${yes(o.control)}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">The firm lists no owners behind its owners. That is usual when
         every direct owner is a person.</p>`}
  </div>`;
}

async function viewAdviser(crd) {
  setBusy("Reading this firm's Form ADV from the SEC — the first time takes a few seconds…");
  const d = await api(`/v1/advisers/${encodeURIComponent(crd)}`);
  const s = d.snapshot || {};
  // Highest foreign share first: on a firm with dozens of funds that is the
  // column a reader came for, and alphabetical order buried it.
  const funds = [...(s.funds || [])].sort((a, b) =>
    (b.owned_by_non_us_pct ?? -1) - (a.owned_by_non_us_pct ?? -1)
    || (b.gross_asset_value || 0) - (a.gross_asset_value || 0));
  const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
  const mostlyForeign = funds.filter((f) => (f.owned_by_non_us_pct ?? 0) > 50).length;
  const abroad = funds.filter((f) => f.country && !/^united states$/i.test(f.country)).length;
  const foreignOwners = (s.owners || []).filter((o) => o.foreign).length;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/portfolio">Portfolio</a> › Investment firm</div>
    <div class="page-head">
      <h1>${esc(s.name || "CRD " + crd)}</h1>
      <div class="sub">CRD ${esc(crd)} · ${esc(s.office || "")}
        · latest Form ADV ${esc(s.filing_date || "unknown")}
        ${s.filing_kind ? "(" + esc(s.filing_kind.toLowerCase()) + ")" : ""}</div>
    </div>

    <div class="grid cols-4">
      ${statCard("Private funds", s.funds_read ? num(funds.length) : "—")}
      ${statCard("Funds more than half owned outside the U.S.", s.funds_read ? num(mostlyForeign) : "—")}
      ${statCard("Funds set up outside the U.S.", s.funds_read ? num(abroad) : "—")}
      ${statCard("Owners of the firm based outside the U.S.", s.owners_read ? num(foreignOwners) : "—")}
    </div>

    <div class="card">
      <h2>Private funds this firm runs</h2>
      <p class="muted">From the firm's own filing — <strong>Schedule D, Section 7.B.(1)</strong>
      of Form ADV. A private fund is a pool of money collected from investors and invested
      on their behalf, often by buying companies. The last column is the share of each fund
      owned by people or organisations based <strong>outside the United States</strong>;
      funds are listed with the highest share first. Who owns the firm itself is
      <a href="#" id="to-owners">further down</a>.</p>
      ${s.funds_read ? (funds.length ? `<table>
        <thead><tr><th>Fund</th><th class="num">Money in the fund</th>
          <th class="num">Investors</th><th>Set up in</th>
          <th class="num">Owned outside the U.S.</th></tr></thead>
        <tbody>${funds.map((f) => `<tr>
          <td>${esc(f.name)}<div class="muted" style="font-size:11.5px">${esc(f.fund_id)}</div></td>
          <td class="num">${f.gross_asset_value === null || f.gross_asset_value === undefined
            ? "—" : money(f.gross_asset_value)}</td>
          <td class="num">${f.investors === null ? "—" : num(f.investors)}</td>
          <td>${esc([f.state, f.country].filter(Boolean).join(", ") || "—")}</td>
          <td class="num"><strong>${pct(f.owned_by_non_us_pct)}</strong></td>
        </tr>`).join("")}</tbody></table>`
        : '<p class="muted">This firm reports no private funds.</p>')
        : '<p class="muted">The funds section could not be read from this filing. The links below open it directly.</p>'}
      <div class="actions">
        <a class="linkish" href="${esc(d.links.summary)}" target="_blank" rel="noopener noreferrer">Firm summary on the SEC's adviser site →</a>
        <a class="linkish" href="${esc(d.links.form)}" target="_blank" rel="noopener noreferrer">Full Form ADV (PDF) →</a>
      </div>
    </div>

    ${ownersCard(s, d.links)}

    ${(s.related_firms || []).length ? `<div class="card">
      <h2>Related firms</h2>
      <p class="muted">Firms often set up a separate partnership to run each fund. A new
      one appearing here often comes with a new fund.</p>
      <ul style="margin:0;padding-left:20px">${s.related_firms.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    </div>` : ""}

    <div class="card">
      <h2>Changes seen</h2>
      ${d.changes.length ? d.changes.map((c) => `
        <div style="border-left:3px solid var(--accent);padding:4px 12px;margin:10px 0">
          <div><strong>${esc(c.headline)}</strong> <span class="muted" style="font-size:12px">· ${esc(day(c.detected_at))}</span></div>
          ${c.detail ? `<div>${esc(c.detail)}</div>` : ""}
          <div class="muted" style="font-size:12.5px"><em>What this is:</em> ${esc(c.explainer)}</div>
          <div class="muted" style="font-size:12.5px"><em>Where to look:</em> ${esc(c.where)}</div>
        </div>`).join("")
        : `<p class="muted">None yet. The first reading is the starting point; changes are
           reported from the next filing on.</p>`}
      <div class="actions"><button id="adv-refresh">Check for a new filing now</button></div>
    </div>`;

  document.getElementById("to-owners").onclick = (e) => {
    e.preventDefault();     // the page routes on the hash, so no #anchor links
    document.getElementById("owners-card")?.scrollIntoView({ behavior: "smooth" });
  };
  document.getElementById("adv-refresh").onclick = async () => {
    setBusy("Checking the SEC for a new filing…");
    await api(`/v1/advisers/${encodeURIComponent(crd)}?refresh=true`);
    viewAdviser(crd);
  };
}

async function viewPortfolio() {
  const key = savedPortfolioKey();
  if (!key) {
    view.innerHTML = portfolioEmpty();
    wirePortfolio(null);
    wireAdvisers();
    return;
  }
  setBusy("Opening portfolio…");
  let d;
  try {
    d = await apiPost("/v1/portfolio", { key });
  } catch (e) {
    // A stored key that no longer loads must not trap the page: show the
    // reason and the form, with the key still in the box to be corrected.
    view.innerHTML = portfolioEmpty(key, e.message || String(e));
    wirePortfolio(null);
    wireAdvisers();
    return;
  }

  const unscreened = d.companies.filter((c) => !c.screened_here && c.kind !== "adviser").length;
  const alertsHtml = await alertsCard(key, d.name);
  view.innerHTML = `
    <div class="page-head">
      <h1>${esc(d.name)}</h1>
      <div class="sub">${num(d.totals.companies)} compan${d.totals.companies === 1 ? "y" : "ies"}
        · saved ${day(d.created_at)}</div>
    </div>

    <div class="grid cols-4">
      ${statCard("Obligated", money(d.totals.obligated))}
      ${statCard("Awards", num(d.totals.contracts))}
      ${statCard("Companies", num(d.totals.companies))}
      ${statCard("Screened here", num(d.totals.screened_here))}
    </div>

    ${Object.keys(d.severity || {}).length ? `
    <div class="card">
      <h2>By latest severity</h2>
      ${severityChart(d.severity)}
    </div>` : ""}

    <div class="card">
      <h2>Companies</h2>
      ${unscreened ? `<div class="muted" style="font-size:12px;margin-bottom:10px">
        ${num(unscreened)} of these ${unscreened === 1 ? "has" : "have"} not been
        screened on this deployment. They are listed because that is the useful
        half of the answer — it is what to screen next, and leaving them out
        would make the portfolio look complete when it is not.</div>` : ""}
      ${portfolioTable(d.companies)}
    </div>

    ${alertsHtml}

    ${advisersCard()}

    <div class="card">
      <h2>Your key</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        This portfolio <strong>is</strong> this text. Keep it in a file: it opens
        the same dashboard in another browser, on another machine, or against a
        database that has been rebuilt from nothing. It is not a password — it
        holds the company names and nothing else — but anyone you send it to can
        open the same list.</div>
      <div class="code" id="portfolio-key" style="white-space:pre-wrap;word-break:break-all">${esc(key)}</div>
      <div class="actions">
        <button class="primary" id="pf-save">Save to a file</button>
        <button id="pf-copy">Copy</button>
        <button class="ghost" id="pf-forget">Forget on this browser</button>
      </div>
    </div>`;
  wirePortfolio(d.name);
  wireAlerts(key, d.name);
  wireAdvisers();
}

function portfolioEmpty(key = "", error = "") {
  return `
    <div class="page-head">
      <h1>Portfolio</h1>
      <div class="sub">A saved dashboard of the companies you watch.</div>
    </div>

    ${error ? `<div class="error"><strong>That key did not open</strong>
      <div class="muted" style="margin-top:6px">${esc(error)}</div></div>` : ""}

    <div class="card">
      <h2>Open a saved portfolio</h2>
      <p class="muted">Paste the key you saved. Line breaks from a text file are
      fine.</p>
      <textarea id="pf-key-input" rows="4" placeholder="FOCI-PORTFOLIO-1.…"
        style="width:100%;font-family:var(--mono);font-size:12.5px">${esc(key)}</textarea>
      <div class="actions"><button class="primary" id="pf-open">Open</button></div>
    </div>

    <div class="card">
      <h2>Or build one</h2>
      <p class="muted">One company per line — a UEI, or the name as it appears on
      an award. You will get a key back to save.</p>
      <input id="pf-name" placeholder="Name this portfolio" style="width:100%;
        padding:8px;border:1px solid var(--border);border-radius:8px;
        background:var(--surface-2);color:var(--text);margin-bottom:8px">
      <textarea id="pf-companies" rows="6" placeholder="UEI123456789&#10;LOCKHEED MARTIN CORPORATION"
        style="width:100%;font-family:var(--mono);font-size:12.5px"></textarea>
      <div class="actions">
        <button class="primary" id="pf-build">Build key</button>
        <button id="pf-build-screened">Use everything screened here</button>
      </div>
    </div>

    ${advisersCard()}`;
}

function downloadPortfolioKey(key, name) {
  const safe = (name || "portfolio").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  const body =
    `# foci-screen portfolio key\n` +
    `# Keep this file. Paste the line below into the Portfolio page to reopen\n` +
    `# this dashboard — on any machine, against any deployment.\n\n${key}\n`;
  const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safe}.foci-key.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function wirePortfolio(loadedName) {
  const open = document.getElementById("pf-open");
  if (open) {
    open.onclick = () => {
      const typed = document.getElementById("pf-key-input").value.trim();
      if (!typed) return;
      rememberPortfolioKey(typed);
      viewPortfolio();
    };
  }

  const build = document.getElementById("pf-build");
  if (build) {
    const mint = async (companies) => {
      if (!companies.length) {
        showError({ title: "Nothing to build",
                    message: "Add at least one company, one per line." });
        return;
      }
      try {
        const d = await apiPost("/v1/portfolio/key", {
          name: document.getElementById("pf-name").value.trim() || "Portfolio",
          companies,
        });
        rememberPortfolioKey(d.key);
        viewPortfolio();
      } catch (e) {
        showError(e);
      }
    };
    build.onclick = () => mint(
      document.getElementById("pf-companies").value
        .split("\n").map((s) => s.trim()).filter(Boolean));

    document.getElementById("pf-build-screened").onclick = async () => {
      const d = await api("/v1/search?q=&kind=entity&limit=100");
      await mint((d.entities || []).map((e) => e.entity_key));
    };
  }

  const save = document.getElementById("pf-save");
  if (save) {
    save.onclick = () => downloadPortfolioKey(savedPortfolioKey(), loadedName);
    document.getElementById("pf-copy").onclick = async () => {
      const button = document.getElementById("pf-copy");
      try {
        await navigator.clipboard.writeText(savedPortfolioKey());
        button.textContent = "Copied";
      } catch {
        // Clipboard access is refused in plenty of ordinary situations.
        button.textContent = "Select the key above to copy";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 2500);
    };
    document.getElementById("pf-forget").onclick = () => {
      rememberPortfolioKey("");
      viewPortfolio();
    };
  }
}

/* --------------------------------------------------------------- screening */

/* What this tenant screens for, and what it wants to be told about. Every
 * label comes from the server's catalogue, so the page cannot offer a choice
 * the engine does not understand. Saving is explicit, and the preview runs
 * the real decision over past findings before anything changes — choosing a
 * notice threshold without seeing what it does is guessing. */
function checkboxes(name, options, selected, hint = {}) {
  return Object.entries(options).map(([value, label]) => `
    <label class="check">
      <input type="checkbox" name="${esc(name)}" value="${esc(value)}"
        ${selected.includes(value) ? "checked" : ""}>
      <span><strong>${esc(value.replace(/_/g, " "))}</strong>
        <span class="muted"> — ${esc(typeof label === "string" ? label : label.label)}</span>
        ${hint[value] ? `<span class="muted" style="font-size:11.5px"> (${esc(hint[value])})</span>` : ""}
      </span>
    </label>`).join("");
}

function readPolicyForm(root) {
  const picked = (name) => [...root.querySelectorAll(`input[name="${name}"]:checked`)]
    .map((i) => i.value);
  return {
    categories: picked("categories"),
    release_kinds: picked("release_kinds"),
    notice_min_severity: root.querySelector("#notice-severity").value,
    notice_mode: (root.querySelector('input[name="notice_mode"]:checked') || {}).value,
    notice_categories: picked("notice_categories"),
    notice_always: picked("notice_always"),
  };
}

function policyNotes(notes) {
  if (!notes || !notes.length) return "";
  return `<div class="card" style="border-left:3px solid var(--medium)">
    <h2>Adjusted when saved</h2>
    <ul class="muted" style="font-size:13px;margin:0;padding-left:20px">
      ${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>`;
}

async function viewScreening(notes) {
  setBusy("Loading screening settings…");
  const d = await api("/v1/policy");
  const p = d.policy;
  const cat = d.catalogue;

  const eventHints = Object.fromEntries(Object.entries(cat.events).map(
    ([k, e]) => [k, e.requires_new ? "only when newly published" : "once, whenever found"]));

  view.innerHTML = `
    <div class="page-head">
      <h1>Screening</h1>
      <div class="sub">What this deployment looks for, and what it asks a reviewer
        to look at. ${d.is_default ? "Currently the defaults." : "Customised."}
        Changes take effect on the next screen.</div>
    </div>

    ${policyNotes(notes || d.notes)}

    <form id="policy-form">
    <div class="card">
      <h2>What to screen for</h2>
      <p class="muted">A category switched off is dropped before anything is
      scored, so it cannot raise a finding or combine with another into one.
      For individual rules and their weights, see <a href="#/rules">Rules</a>.</p>
      <div class="checks">${checkboxes("categories", cat.categories, p.categories)}</div>

      <h3 style="margin-top:18px">Company releases to read</h3>
      <p class="muted">From release feeds and saved alert emails. A kind switched
      off is not read at all, so it builds no history either.</p>
      <div class="checks">${checkboxes("release_kinds", cat.release_kinds, p.release_kinds)}</div>
    </div>

    <div class="card">
      <h2>What raises a notice</h2>
      <p class="muted">A notice is a draft for a person to read, edit and approve
      — nothing here sends anything. <strong>Each piece of evidence notifies
      once</strong>: after that it has been said, and a rejected notice counts, so
      a reviewer's "no" is not overruled by the next screen.</p>

      <label class="field">Notify for findings at or above
        <select id="notice-severity">
          ${cat.notice_severities.map((s) =>
            `<option value="${s}" ${s === p.notice_min_severity ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </label>

      <h3 style="margin-top:14px">When</h3>
      <div class="checks">
        ${Object.entries(cat.notice_modes).map(([k, label]) => `
          <label class="check"><input type="radio" name="notice_mode" value="${esc(k)}"
            ${k === p.notice_mode ? "checked" : ""}>
            <span><strong>${esc(k.replace(/_/g, " "))}</strong>
              <span class="muted"> — ${esc(label)}</span></span></label>`).join("")}
      </div>

      <h3 style="margin-top:14px">Categories that may raise a notice</h3>
      <div class="checks">${checkboxes("notice_categories",
        Object.fromEntries(Object.entries(cat.categories).filter(([k]) => k !== "DISCLOSURE")),
        p.notice_categories)}</div>

      <h3 style="margin-top:14px">Always notify me when</h3>
      <p class="muted">Regardless of the threshold above — still once each.</p>
      <div class="checks">${checkboxes("notice_always", cat.events, p.notice_always, eventHints)}</div>
    </div>

    <div class="card">
      <h2>Try it first</h2>
      <p class="muted">Runs these settings over each contractor's latest finding,
      remembering what has already been notified. Nothing is saved or queued.</p>
      <div class="actions"><button type="button" id="policy-preview">Preview</button></div>
      <div id="policy-preview-out" style="margin-top:10px"></div>
    </div>

    <div class="actions">
      <button type="submit" class="primary">Save</button>
      <button type="button" class="ghost" id="policy-reset">Reset to defaults</button>
    </div>
    </form>`;

  const form = document.getElementById("policy-form");

  document.getElementById("policy-preview").onclick = async () => {
    const out = document.getElementById("policy-preview-out");
    out.textContent = "Running…";
    try {
      const r = await apiPost("/v1/policy/preview", readPolicyForm(form));
      out.innerHTML = `
        <p><strong>${num(r.would_raise)}</strong> of ${num(r.contractors)} contractor(s)
        would get a new notice.</p>
        ${r.examples.length ? `<table><thead><tr><th>Contractor</th><th>Severity</th>
          <th>Why</th></tr></thead><tbody>${r.examples.map((e) => `
          <tr><td>${linkEntity(e.entity_key, e.entity_name)}</td><td>${sevTag(e.severity)}</td>
          <td class="muted" style="font-size:12.5px">${esc(e.reason)}</td></tr>`).join("")}
          </tbody></table>` : ""}
        ${r.held.length ? `<p class="muted" style="margin-top:10px">Held back:</p>
          <ul class="muted" style="font-size:12.5px;padding-left:20px">
          ${r.held.map((h) => `<li>${num(h.count)} — ${esc(h.reason)}</li>`).join("")}</ul>` : ""}
        ${(r.notes || []).length ? `<p class="muted" style="font-size:12.5px">
          ${r.notes.map(esc).join(" ")}</p>` : ""}`;
    } catch (e) {
      out.textContent = e.message || String(e);
    }
  };

  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await apiSend("PUT", "/v1/policy", readPolicyForm(form));
      viewScreening(r.notes);
    } catch (err) {
      showError(err);
    }
  };

  document.getElementById("policy-reset").onclick = async () => {
    await apiSend("DELETE", "/v1/policy");
    viewScreening([]);
  };
}

/* ------------------------------------------------------------------ router */

const ROUTES = [
  [/^\/?$/, () => viewOverview()],
  [/^\/search\/([^/]*)(?:\/([^/]*))?$/, (q, k) => viewSearch(decodeURIComponent(q), k)],
  [/^\/entity\/(.+)$/, (k) => viewEntity(decodeURIComponent(k))],
  [/^\/officer\/(.+)$/, (e) => viewOfficer(decodeURIComponent(e))],
  [/^\/agency\/(.+)$/, (n) => viewAgency(decodeURIComponent(n))],
  [/^\/contract\/(.+)$/, (c) => viewContract(decodeURIComponent(c))],
  [/^\/diff\/([^/]+)\/([^/]+)(?:\/([^/]+))?$/,
   (s, k, from, params) => viewDiff(decodeURIComponent(s), decodeURIComponent(k),
                                    from ? decodeURIComponent(from) : "",
                                    (params && params.get("entity")) || "")],
  [/^\/notices$/, () => viewNotices()],
  [/^\/rules$/, () => viewRules()],
  [/^\/portfolio$/, () => viewPortfolio()],
  [/^\/screening$/, () => viewScreening()],
  [/^\/adviser\/(\d+)$/, (crd) => viewAdviser(crd)],
  [/^\/screens\/(.+)$/, (id) => viewScreen(decodeURIComponent(id))],
];

async function route() {
  // The screen view polls itself. Left running, it would redraw its own page
  // over whatever the reader navigated to.
  if (screenPollTimer) {
    clearTimeout(screenPollTimer);
    screenPollTimer = null;
  }
  const raw = location.hash.replace(/^#/, "") || "/";
  // Split the query off before matching: otherwise `?entity=…` lands inside
  // the last path segment and every key gains a suffix.
  const [path, queryString] = raw.split("?");
  const params = new URLSearchParams(queryString || "");

  for (const [re, handler] of ROUTES) {
    const m = path.match(re);
    if (m) {
      try {
        await handler(...m.slice(1), params);
      } catch (e) {
        showError(e);
      }
      refreshBadge();
      return;
    }
  }
  showError({ title: "Not found", message: `No view for ${path}` });
}

/* Configuration that loses data is silent by design: the service answers, the
 * dashboard renders, and nothing on it says the disk is about to be discarded.
 * /health needs no key, so this shows even while the API is closed. */
/* Whether this deployment can keep what it is given. Read by the empty state:
 * "nothing has been screened" and "what was screened has been thrown away"
 * look identical from here, and only one of them is the reader's fault. */
let storageIsEphemeral = false;

async function refreshHealth() {
  const banner = document.getElementById("config-banner");
  if (!banner) return;
  let health;
  try {
    const res = await fetch("/health");
    if (!res.ok) return;
    health = await res.json();
  } catch {
    return;   // unreachable server; the view's own error already says so
  }
  storageIsEphemeral = health.ephemeral_storage === true;
  const warnings = health.warnings || [];
  if (!warnings.length) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  banner.innerHTML = `<strong>Check this deployment's configuration</strong>
    <ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
  banner.hidden = false;
}

async function refreshBadge() {
  const badge = document.getElementById("notice-badge");
  try {
    const d = await api("/v1/notices?status=pending&limit=200");
    const n = d.notices.length;
    badge.textContent = n;
    badge.hidden = n === 0;
  } catch {
    badge.hidden = true;
  }
}

/* ------------------------------------------------------------------- wiring */

/* ------------------------------------------------------------- type-ahead */

/* Suggestions while typing, from two places that mean different things: what
 * this database has screened, and contractors that exist in federal
 * contracting but have never been screened here.
 *
 * Nothing here screens anything. A screen takes minutes and walks half a dozen
 * government APIs; firing one per keystroke would be absurd and rude. Picking
 * an unscreened name offers a screen — it does not start one.
 *
 * The remote half is best effort by necessity. USAspending's autocomplete is
 * fast on a long prefix and times out on a short one, which is the opposite of
 * what a type-ahead wants, so it is debounced, capped, cached per prefix on
 * the server, and allowed to come back empty. The box never waits on it. */
const SUGGEST_DEBOUNCE_MS = 220;

let suggestTimer = null;
let suggestSeq = 0;
let suggestItems = [];
let suggestActive = -1;

const searchInput = document.getElementById("search-input");
const suggestBox = document.getElementById("suggest-box");

function hideSuggestions() {
  suggestItems = [];
  suggestActive = -1;
  if (suggestBox) {
    suggestBox.hidden = true;
    suggestBox.innerHTML = "";
  }
}

function renderSuggestions(items, query) {
  suggestItems = items;
  suggestActive = -1;
  if (!items.length) {
    hideSuggestions();
    return;
  }
  const icon = { entity: "contractor", officer: "officer", agency: "agency",
                 unscreened: "screen this" };
  suggestBox.innerHTML = items.map((item, i) => `
    <div class="suggest-row" data-i="${i}" role="option">
      <span class="suggest-label">${esc(item.label)}</span>
      <span class="suggest-kind">${esc(icon[item.kind] || item.kind)}</span>
      <span class="suggest-detail">${esc(item.detail || "")}</span>
    </div>`).join("") +
    `<div class="suggest-foot muted">Enter to search “${esc(query)}”</div>`;
  suggestBox.hidden = false;

  suggestBox.querySelectorAll(".suggest-row").forEach((row) => {
    row.onmousedown = (e) => {      // mousedown: blur would close it first
      e.preventDefault();
      chooseSuggestion(Number(row.dataset.i));
    };
  });
}

function highlightSuggestion(next) {
  const rows = suggestBox.querySelectorAll(".suggest-row");
  if (!rows.length) return;
  suggestActive = (next + rows.length) % rows.length;
  rows.forEach((r, i) => r.classList.toggle("active", i === suggestActive));
}

function chooseSuggestion(index) {
  const item = suggestItems[index];
  if (!item) return;
  hideSuggestions();
  searchInput.value = item.label;
  if (item.kind === "entity") location.hash = `#/entity/${encodeURIComponent(item.key)}`;
  else if (item.kind === "officer") location.hash = `#/officer/${encodeURIComponent(item.key)}`;
  else if (item.kind === "agency") location.hash = `#/agency/${encodeURIComponent(item.key)}`;
  // An unscreened contractor goes to the search page, which is where the
  // offer to screen it lives. Still a choice, not a screen.
  else location.hash = `#/search/${encodeURIComponent(item.key)}`;
}

async function fetchSuggestions(q) {
  const seq = ++suggestSeq;
  try {
    const d = await api(`/v1/suggest?q=${encodeURIComponent(q)}`);
    // A slower earlier request must not overwrite a newer answer.
    if (seq !== suggestSeq) return;
    renderSuggestions([...(d.local || []), ...(d.remote || [])], q);
  } catch {
    if (seq === suggestSeq) hideSuggestions();
  }
}

if (searchInput && suggestBox) {
  searchInput.setAttribute("autocomplete", "off");
  searchInput.oninput = () => {
    const q = searchInput.value.trim();
    if (suggestTimer) clearTimeout(suggestTimer);
    if (q.length < 2) {
      hideSuggestions();
      return;
    }
    suggestTimer = setTimeout(() => fetchSuggestions(q), SUGGEST_DEBOUNCE_MS);
  };

  searchInput.onkeydown = (e) => {
    if (suggestBox.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); highlightSuggestion(suggestActive + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlightSuggestion(suggestActive - 1); }
    else if (e.key === "Escape") hideSuggestions();
    else if (e.key === "Enter" && suggestActive >= 0) {
      e.preventDefault();
      chooseSuggestion(suggestActive);
    }
  };

  searchInput.onblur = () => setTimeout(hideSuggestions, 120);
}

document.getElementById("search-form").onsubmit = (e) => {
  e.preventDefault();
  if (suggestActive >= 0 && !suggestBox.hidden) {
    chooseSuggestion(suggestActive);
    return;
  }
  hideSuggestions();
  const q = document.getElementById("search-input").value.trim();
  location.hash = `#/search/${encodeURIComponent(q)}`;
};

document.getElementById("key-button").onclick = () => {
  document.getElementById("key-input").value = apiKey;
  keyDialog.showModal();
};

document.getElementById("key-form").onsubmit = () => {
  if (keyDialog.returnValue !== "cancel") {
    apiKey = document.getElementById("key-input").value.trim();
    try {
      localStorage.setItem(KEY_STORAGE, apiKey);
    } catch { /* storage blocked; key lives for this page only */ }
    route();
  }
};
keyDialog.addEventListener("close", () => {
  if (keyDialog.returnValue === "save") {
    apiKey = document.getElementById("key-input").value.trim();
    try {
      localStorage.setItem(KEY_STORAGE, apiKey);
    } catch { /* storage blocked */ }
    route();
  }
});

window.addEventListener("hashchange", route);
route();
refreshHealth();
