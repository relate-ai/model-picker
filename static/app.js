const state = {
  catalog: null,
  models: [],
  filtered: [],
  selected: null,
  compare: new Set(),
  routing: null,
  telemetry: null,
  token: localStorage.getItem("modelPickerToken") || "",
};

const $ = (id) => document.getElementById(id);
const money = (v) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 }));

async function fetchJson(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text.slice(0, 300)}`);
  }
  return res.json();
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function renderTopStats() {
  const data = state.catalog || {};
  const models = state.models;
  const providers = new Set(models.map((m) => m.provider));
  const free = models.filter((m) => m.billing_type === "free").length;
  const stats = [
    ["Models", models.length],
    ["Providers", providers.size],
    ["Free", free],
    ["Catalog SHA", (data.catalog_sha256 || "").slice(0, 12) || "—"],
  ];
  $("topStats").innerHTML = stats.map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join("");
  setText("catalogMeta", `${models.length} offerings from ${providers.size} providers. Source: ${data._source_path || "host storage"}. Catalog SHA-256: ${data.catalog_sha256 || "unknown"}.`);
}

function populateProviderFilter() {
  const providers = [...new Set(state.models.map((m) => m.provider))].sort();
  const select = $("providerFilter");
  const current = select.value;
  select.innerHTML = '<option value="">All providers</option>' + providers.map((p) => `<option value="${p}">${p}</option>`).join("");
  select.value = current;
}

function applyFilters() {
  const q = $("searchInput").value.trim().toLowerCase();
  const provider = $("providerFilter").value;
  const billing = $("billingFilter").value;
  const capability = $("capabilityFilter").value;
  let items = state.models.filter((m) => {
    if (provider && m.provider !== provider) return false;
    if (billing && m.billing_type !== billing) return false;
    if (capability && !(m.capabilities || []).includes(capability)) return false;
    if (q && !JSON.stringify(m).toLowerCase().includes(q)) return false;
    return true;
  });
  const sort = $("sortSelect").value;
  const reverse = sort.startsWith("-");
  const key = sort.replace("-", "");
  items.sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av < bv) return reverse ? 1 : -1;
    if (av > bv) return reverse ? -1 : 1;
    return 0;
  });
  state.filtered = items;
  renderTable();
}

function sortValue(m, key) {
  if (key === "id") return m.id || "";
  if (key === "provider") return m.provider || "";
  if (key === "input_usd_per_million") return Number((m.effective_rates || {}).input_usd_per_million || 0);
  if (key === "output_usd_per_million") return Number((m.effective_rates || {}).output_usd_per_million || 0);
  if (key === "context") return Number((m.host_model_ref || {}).context_limit || 0);
  return m[key] || "";
}

function renderTable() {
  const tbody = $("modelTable").querySelector("tbody");
  tbody.innerHTML = state.filtered.map((m) => {
    const rates = m.effective_rates || {};
    const ctx = (m.host_model_ref || {}).context_limit;
    const checked = state.compare.has(m.id) ? "checked" : "";
    return `<tr data-id="${m.id}" tabindex="0">
      <td><strong>${m.id}</strong><div class="muted">${m.pool || ""}</div></td>
      <td>${m.provider}<div class="muted">${m.billing_type}</div></td>
      <td>${m.billing_type}</td>
      <td>${(m.capabilities || []).join(", ")}</td>
      <td>${ctx ? ctx.toLocaleString() : "—"}</td>
      <td>${money(rates.input_usd_per_million)}</td>
      <td>${money(rates.output_usd_per_million)}</td>
      <td>${money(rates.cached_input_usd_per_million)}</td>
      <td><input type="checkbox" class="compare-check" data-id="${m.id}" ${checked} aria-label="Compare ${m.id}" /></td>
    </tr>`;
  }).join("");
  $("catalogEmpty").hidden = state.filtered.length !== 0;
  tbody.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.classList.contains("compare-check")) return;
      selectModel(tr.dataset.id);
    });
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter") selectModel(tr.dataset.id);
    });
  });
  tbody.querySelectorAll(".compare-check").forEach((cb) => {
    cb.addEventListener("change", () => toggleCompare(cb.dataset.id, cb.checked));
  });
}

function selectModel(id) {
  const m = state.models.find((x) => x.id === id);
  if (!m) return;
  state.selected = m;
  const rates = m.effective_rates || {};
  const ref = m.host_model_ref || {};
  const rows = [
    ["Model ID", m.id],
    ["Provider", m.provider],
    ["Pool", m.pool],
    ["Billing", m.billing_type],
    ["Available", String(m.available)],
    ["Authorized", String(m.authorized)],
    ["Capabilities", (m.capabilities || []).join(", ")],
    ["Context limit", ref.context_limit ? ref.context_limit.toLocaleString() : "UNKNOWN"],
    ["Input $/M", money(rates.input_usd_per_million)],
    ["Output $/M", money(rates.output_usd_per_million)],
    ["Cache read $/M", money(rates.cached_input_usd_per_million)],
    ["Source ref", rates.source_ref || "UNKNOWN"],
    ["Dispatch ref", ref.dispatch_ref || "UNKNOWN"],
    ["Evidence", ref.evidence || "UNKNOWN"],
    ["Snapshot SHA", m.source_snapshot_sha256 || "UNKNOWN"],
  ];
  $("detailPanel").innerHTML = rows.map(([k, v]) => `<div class="detail-row"><strong>${k}</strong><span>${v}</span></div>`).join("");
}

function toggleCompare(id, checked) {
  if (checked) {
    if (state.compare.size >= 4) {
      state.compare.delete([...state.compare][0]);
    }
    state.compare.add(id);
  } else {
    state.compare.delete(id);
  }
  renderCompare();
  renderTable();
}

function renderCompare() {
  const grid = $("compareGrid");
  const selected = [...state.compare].map((id) => state.models.find((m) => m.id === id)).filter(Boolean);
  if (!selected.length) {
    grid.innerHTML = '<p class="muted">Select up to four models using the Compare checkboxes.</p>';
    return;
  }
  grid.innerHTML = selected.map((m) => {
    const rates = m.effective_rates || {};
    const ref = m.host_model_ref || {};
    return `<article class="compare-card">
      <h3>${m.id}</h3>
      <dl>
        <dt>Provider</dt><dd>${m.provider}</dd>
        <dt>Billing</dt><dd>${m.billing_type}</dd>
        <dt>Capabilities</dt><dd>${(m.capabilities || []).join(", ")}</dd>
        <dt>Context</dt><dd>${ref.context_limit ? ref.context_limit.toLocaleString() : "UNKNOWN"}</dd>
        <dt>Input $/M</dt><dd>${money(rates.input_usd_per_million)}</dd>
        <dt>Output $/M</dt><dd>${money(rates.output_usd_per_million)}</dd>
        <dt>Cache read $/M</dt><dd>${money(rates.cached_input_usd_per_million)}</dd>
        <dt>Source</dt><dd>${rates.source_ref || "UNKNOWN"}</dd>
      </dl>
    </article>`;
  }).join("");
}

async function loadCatalog() {
  const data = await fetchJson("/api/catalog");
  state.catalog = data;
  state.models = (data.catalog && data.catalog.models) || [];
  populateProviderFilter();
  renderTopStats();
  applyFilters();
  renderCompare();
}

async function loadRouting() {
  try {
    const data = await fetchJson("/api/routing-matrix");
    state.routing = data;
    const cases = data.cases || [];
    setText("routingMeta", `${cases.length} cases. Source: ${data._source_path || "unknown"}. SHA-256: ${(data._source_sha256 || "").slice(0, 12) || "unknown"}.`);
    $("routingCases").innerHTML = cases.map((c) => {
      const mode = c.execution_mode === "REAL" ? "real" : "dry";
      const stop = c.decision === "STOP" ? "stop" : "";
      return `<article class="routing-case">
        <h3>${c.case_id || c.id || "Case"} <span class="badge ${mode}">${c.execution_mode || "DRY_RUN"}</span> ${stop ? '<span class="badge stop">STOP</span>' : ""}</h3>
        <div class="routing-meta">${c.task_class || ""} · ${c.decision || ""} · ${c.chosen_model || "no model"} · estimated upper bound: ${c.estimated_usd || "0"}</div>
        <div>${c.summary || c.rationale || ""}</div>
        <div class="muted">Rejected: ${(c.rejected || []).join(", ") || "none"}</div>
      </article>`;
    }).join("") || '<p class="muted">No routing matrix loaded.</p>';
  } catch (err) {
    setText("routingMeta", `Routing matrix unavailable: ${err.message}`);
  }
}

function barRows(container, entries, valueKey) {
  const max = Math.max(1, ...entries.map((e) => e[valueKey] || 0));
  container.innerHTML = entries.map((e) => {
    const label = e.model || e.provider;
    const value = e[valueKey] || 0;
    const width = Math.max(2, (value / max) * 100);
    return `<div class="bar-row">
      <div title="${label}">${label}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
      <div>${value}</div>
    </div>`;
  }).join("") || '<p class="muted">No data.</p>';
}

async function loadTelemetry() {
  try {
    const summary = await fetchJson("/api/telemetry/summary");
    state.telemetry = summary;
    const totals = summary.totals || {};
    setText("telemetryMeta", `${summary.event_count} invocation events. ${summary.success_count} successful, ${summary.failure_count} failed.`);
    $("telemetryStats").innerHTML = [
      ["Events", summary.event_count],
      ["Successes", summary.success_count],
      ["Input tokens", (totals.input_tokens || 0).toLocaleString()],
      ["Output tokens", (totals.output_tokens || 0).toLocaleString()],
      ["Cache read", (totals.cache_read_tokens || 0).toLocaleString()],
      ["Cost USD", money(totals.cost_usd)],
    ].map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join("");
    barRows($("modelBars"), summary.by_model || [], "calls");
    barRows($("providerBars"), summary.by_provider || [], "calls");
    const events = await fetchJson("/api/telemetry/events?limit=500");
    const tbody = $("telemetryTable").querySelector("tbody");
    tbody.innerHTML = (events.events || []).map((e) => {
      const t = e.tokens || {};
      return `<tr>
        <td>${e.timestamp_utc || e.start_utc || "—"}</td>
        <td>${e.model_used || e.model_selected || "—"}</td>
        <td>${e.provider || "—"}</td>
        <td>${e.task_class || "—"}</td>
        <td>${e.wall_time_ms ?? "—"}</td>
        <td>${(t.input || 0).toLocaleString()}</td>
        <td>${(t.output || 0).toLocaleString()}</td>
        <td>${(t.cache_read || 0).toLocaleString()}</td>
        <td>${money(e.cost_usd)}</td>
        <td>${e.success ? "OK" : (e.failure_reason || "FAIL")}</td>
      </tr>`;
    }).join("") || '<tr><td colspan="10" class="muted">No telemetry events.</td></tr>';
  } catch (err) {
    setText("telemetryMeta", `Telemetry requires private token: ${err.message}`);
  }
}

async function loadRefresh() {
  try {
    const data = await fetchJson("/api/refresh-status");
    $("refreshPanel").textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    $("refreshPanel").textContent = JSON.stringify({ error: err.message }, null, 2);
  }
}

async function refreshAll() {
  await Promise.allSettled([loadCatalog(), loadRouting(), loadTelemetry(), loadRefresh()]);
}

$("saveToken").addEventListener("click", () => {
  state.token = $("tokenInput").value.trim();
  localStorage.setItem("modelPickerToken", state.token);
  loadTelemetry();
});
$("tokenInput").value = state.token;
$("refreshBtn").addEventListener("click", refreshAll);
$("searchInput").addEventListener("input", applyFilters);
$("providerFilter").addEventListener("change", applyFilters);
$("billingFilter").addEventListener("change", applyFilters);
$("capabilityFilter").addEventListener("change", applyFilters);
$("sortSelect").addEventListener("change", applyFilters);
$("clearCompare").addEventListener("click", () => {
  state.compare.clear();
  renderCompare();
  renderTable();
});

refreshAll();
PY