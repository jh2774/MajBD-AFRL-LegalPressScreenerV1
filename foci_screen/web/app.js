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
    throw { title: "Request failed", message: detail };
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

async function viewOverview() {
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

  view.innerHTML = `
    <div class="page-head">
      <h1>Overview</h1>
      <div class="sub">What has been screened, and what changed.</div>
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
    ${total === 0 ? '<div class="card"><div class="empty">Nothing matched. Only screened awards are searchable — run a screen first.</div></div>' : ""}
    ${section("Contractors", entitiesTable(d.entities || []), d.entities)}
    ${section("Awards", contractsTable(d.contracts || []), d.contracts)}
    ${section("Contracting officers", officersTable(d.officers || []), d.officers)}
    ${section("Agencies", agenciesTable(d.agencies || []), d.agencies)}`;

  view.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => {
      location.hash = `#/search/${encodeURIComponent(q)}/${b.dataset.kind}`;
    };
  });
}

async function viewEntity(key) {
  setBusy("Loading contractor…");
  const [d, docs, verdicts, identity] = await Promise.all([
    api(`/v1/entities/${encodeURIComponent(key)}`),
    api(`/v1/documents?entity_key=${encodeURIComponent(key)}`).catch(() => ({ documents: [] })),
    api(`/v1/entities/${encodeURIComponent(key)}/dispositions`)
      .catch(() => ({ dispositions: {} })),
    api(`/v1/entities/${encodeURIComponent(key)}/identity`).catch(() => null),
  ]);
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

    ${recordChanges(d.record_changes || [])}

    <div class="card">
      <h2>Awards</h2>
      ${truncationNote(d.contracts_shown, d.contract_count)}
      ${contractsTable(d.contracts || [])}
    </div>`;

  wireVerdicts(view, key, f ? f.run_id : "");
  wireIdentity(view, key);
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

    <div class="card">
      <h2>Awards</h2>
      ${truncationNote(d.contracts_shown, d.contract_count)}
      ${contractsTable(d.contracts)}
    </div>`;
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

    <div class="card"><h2>Contracting officers</h2>${officersTable(d.officers)}</div>`;
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
      <div class="card"><div class="empty">No notices yet. They are created for
      findings at medium severity or above.</div></div>`;
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

function portfolioTable(companies) {
  const rows = companies.map((r) => `
    <tr>
      <td>${linkEntity(r.entity_key, r.entity_name)}</td>
      <td>${r.screened_here ? sevTag(r.severity) : '<span class="pill">not screened here</span>'}</td>
      <td class="num">${r.contract_count ? num(r.contract_count) : "—"}</td>
      <td class="num">${r.obligated ? money(r.obligated) : "—"}</td>
      <td>${r.last_screened ? day(r.last_screened) : "—"}</td>
    </tr>`).join("");
  return `<table>
    <thead><tr><th>Company</th><th>Latest severity</th><th class="num">Awards</th>
      <th class="num">Obligated</th><th>Screened</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function viewPortfolio() {
  const key = savedPortfolioKey();
  if (!key) {
    view.innerHTML = portfolioEmpty();
    wirePortfolio(null);
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
    return;
  }

  const unscreened = d.companies.filter((c) => !c.screened_here).length;
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
    </div>`;
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
];

async function route() {
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

document.getElementById("search-form").onsubmit = (e) => {
  e.preventDefault();
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
