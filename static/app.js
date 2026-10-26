"use strict";
// All user text is written with textContent (never innerHTML), so stored values cannot inject markup.
const $ = (s, r = document) => r.querySelector(s);
const state = { csrf: "", user: null, projects: [], pid: null, data: null, mode: "login", charts: {} };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

async function api(path, opts = {}) {
  const o = { method: "GET", headers: {}, ...opts };
  if (o.method !== "GET") o.headers["X-CSRF-Token"] = state.csrf;
  if (o.json !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.json); }
  const r = await fetch(path, { credentials: "same-origin", ...o });
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) {
    if (r.status === 401 && state.user) { showAuth(); }
    throw new Error((body && body.error) || "Something went wrong (" + r.status + ").");
  }
  return body;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 3500);
}

const cur = () => (state.data ? state.data.project.currency : "Rs.");
const money = x => x == null ? "–" : cur() + " " + Math.round(x).toLocaleString("en-US");
const signed = x => { if (x == null) return "–"; const r = Math.round(x); return (r > 0 ? "+" : r < 0 ? "−" : "") + cur() + " " + Math.abs(r).toLocaleString("en-US"); };
const pct = x => { if (x == null) return "–"; const r = Math.round(x * 10) / 10; return (r > 0 ? "+" : r < 0 ? "−" : "") + Math.abs(r).toFixed(1) + "%"; };
const cls = x => x == null || Math.abs(x) < 0.5 ? "" : x > 0 ? "over" : "under";

// ---------- auth ----------
function showAuth() {
  state.user = null;
  $("#app").hidden = true; $("#auth").hidden = false;
  $("#authError").textContent = "";
}
function setMode(m) {
  state.mode = m;
  $("#authSubmit").textContent = m === "login" ? "Sign in" : "Create account";
  $("#authToggle").textContent = m === "login" ? "Create an account" : "I already have an account";
  $("#authPass").autocomplete = m === "login" ? "current-password" : "new-password";
}
$("#authToggle").onclick = () => setMode(state.mode === "login" ? "register" : "login");
$("#authForm").onsubmit = async e => {
  e.preventDefault();
  try {
    const r = await api("/api/" + (state.mode === "login" ? "login" : "register"),
      { method: "POST", json: { username: $("#authUser").value, password: $("#authPass").value } });
    state.csrf = r.csrf; state.user = r.user;
    $("#authPass").value = "";
    await enterApp();
  } catch (x) { $("#authError").textContent = x.message; }
};
$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }); const me = await api("/api/me"); state.csrf = me.csrf; showAuth(); };

async function enterApp() {
  $("#auth").hidden = true; $("#app").hidden = false;
  $("#whoName").textContent = state.user.username;
  await loadProjects();
}

// ---------- projects ----------
async function loadProjects(selectId) {
  state.projects = await api("/api/projects");
  const sel = $("#projectSelect");
  sel.replaceChildren(...state.projects.map(p => h("option", { value: p.id }, p.name)));
  state.pid = selectId || (state.projects.find(p => p.id === state.pid) || state.projects[0] || {}).id || null;
  if (state.pid) sel.value = state.pid;
  $("#empty").hidden = !!state.pid; $("#workspace").hidden = !state.pid;
  $("#editProject").disabled = !state.pid;
  if (state.pid) await refresh();
}
$("#projectSelect").onchange = e => { state.pid = +e.target.value; $("#aiOut").replaceChildren(); refresh(); };

let editing = false;
function openProject(edit) {
  editing = edit;
  const f = $("#projForm"); f.reset();
  $("#projTitle").textContent = edit ? "Project settings" : "New project";
  $("#projDelete").hidden = !edit; $("#projError").textContent = "";
  if (edit) { const p = state.data.project; f.name.value = p.name; f.currency.value = p.currency; f.contingency_pct.value = p.contingency_pct; }
  $("#projDialog").showModal();
}
$("#newProject").onclick = () => openProject(false);
$("#editProject").onclick = () => openProject(true);
$("#projCancel").onclick = () => $("#projDialog").close();
$("#projSave").onclick = async e => {
  e.preventDefault();
  const f = $("#projForm");
  if (!f.reportValidity()) return;
  const json = { name: f.name.value, currency: f.currency.value, contingency_pct: f.contingency_pct.value };
  try {
    if (editing) { await api("/api/projects/" + state.pid, { method: "PUT", json }); await loadProjects(state.pid); }
    else { const p = await api("/api/projects", { method: "POST", json }); await loadProjects(p.id); }
    $("#projDialog").close(); toast("Project saved.");
  } catch (x) { $("#projError").textContent = x.message; }
};
$("#projDelete").onclick = async () => {
  if (!confirm("Delete this project and all its costs? This cannot be undone.")) return;
  await api("/api/projects/" + state.pid, { method: "DELETE" });
  state.pid = null; $("#projDialog").close(); await loadProjects(); toast("Project deleted.");
};
$("#loadDemo").onclick = async () => {
  try { const p = await api("/api/demo", { method: "POST" }); await loadProjects(p.id); toast("Demo project loaded. Actual costs in it are sample values."); }
  catch (x) { toast(x.message); }
};

// ---------- data ----------
async function refresh() {
  state.data = await api("/api/projects/" + state.pid + "/analysis");
  renderKpis(); renderTable(); renderCharts();
  $("#dlPdf").href = "/api/projects/" + state.pid + "/report.pdf";
  $("#dlXlsx").href = "/api/projects/" + state.pid + "/report.xlsx";
  $("#catList").replaceChildren(...[...new Set(state.data.rows.map(r => r.category))].map(c => h("option", { value: c })));
}

function renderKpis() {
  const s = state.data.summary;
  $("#kEst").textContent = money(s.estimated_total);
  $("#kAct").textContent = money(s.actual_total);
  const v = $("#kVar"), p = $("#kPct");
  v.textContent = signed(s.variance); p.textContent = pct(s.pct);
  v.className = p.className = cls(s.variance);
  $("#kFor").textContent = money(s.forecast);
  $("#kNote").textContent = s.items_with_actual + " of " + s.items + " items have an actual cost. Variance compares only those items (estimate " +
    money(s.est_compared) + "). Contingency " + s.contingency_pct + "% = " + money(s.contingency_amount) + ".";
}

function badge(r) {
  const c = r.status === "Over budget" ? "over" : r.status === "Under budget" ? "under" : r.status === "Unbudgeted" ? "warn" : "";
  return h("span", { class: "badge " + c }, r.status);
}

function varCell(r) {
  if (r.variance == null) return h("td", { class: "num" }, "–");
  const fill = h("span", { class: "vfill " + cls(r.variance) });
  const w = r.pct == null ? (r.variance > 0 ? 50 : 0) : Math.min(Math.abs(r.pct), 50);
  fill.style.width = (w) + "%";
  const track = h("span", { class: "vtrack", "aria-hidden": "true" }, fill);
  return h("td", { class: "num" }, h("div", { class: "vbar" }, h("span", { class: cls(r.variance) }, signed(r.variance)), track));
}

async function saveRow(r, tr) {
  const est = $(".est", tr).value, act = $(".act", tr).value;
  try {
    await api("/api/projects/" + state.pid + "/items/" + r.id, { method: "PUT",
      json: { category: r.category, item: r.item, cost_type: r.cost_type, estimated: est, actual: act } });
    await refresh();
  } catch (x) { toast(x.message); await refresh(); }
}

function renderTable() {
  const q = $("#search").value.trim().toLowerCase(), st = $("#statusFilter").value;
  const rows = state.data.rows.filter(r => (!st || r.status === st) && (!q || (r.item + " " + r.category).toLowerCase().includes(q)));
  const body = $("#costTable tbody");
  if (!rows.length) {
    body.replaceChildren(h("tr", {}, h("td", { colspan: 9, class: "muted" }, state.data.rows.length ? "No items match this filter." : "No costs yet. Add a cost or upload a file above.")));
  } else {
    body.replaceChildren(...rows.map(r => {
      const est = h("input", { class: "est", type: "number", min: "0", step: "any", value: Math.round(r.estimated * 100) / 100, "aria-label": "Estimated cost for " + r.item });
      const act = h("input", { class: "act", type: "number", min: "0", step: "any", value: r.actual == null ? "" : r.actual, placeholder: "not yet", "aria-label": "Actual cost for " + r.item });
      const tr = h("tr", {},
        h("td", { class: "cat" }, r.category), h("td", {}, r.item), h("td", {}, r.cost_type),
        h("td", { class: "num" }, est), h("td", { class: "num" }, act),
        varCell(r), h("td", { class: "num " + cls(r.variance) }, pct(r.pct)), h("td", {}, badge(r)),
        h("td", {}, h("button", { class: "xbtn", "aria-label": "Delete " + r.item, onclick: async () => {
          if (!confirm("Delete “" + r.item + "”?")) return;
          await api("/api/projects/" + state.pid + "/items/" + r.id, { method: "DELETE" }); toast("Deleted."); refresh();
        } }, "Delete")));
      est.onchange = act.onchange = () => saveRow(r, tr);
      return tr;
    }));
  }
  const s = state.data.summary;
  $("#costTable tfoot").replaceChildren(h("tr", {}, h("td", { colspan: 3 }, "Total"), h("td", { class: "num" }, money(s.estimated_total)),
    h("td", { class: "num" }, money(s.actual_total)), h("td", { class: "num " + cls(s.variance) }, signed(s.variance)),
    h("td", { class: "num " + cls(s.variance) }, pct(s.pct)), h("td", { colspan: 2 })));
}
$("#search").oninput = $("#statusFilter").onchange = () => state.data && renderTable();

$("#addForm").onsubmit = async e => {
  e.preventDefault();
  const f = e.target, json = Object.fromEntries(new FormData(f));
  try {
    await api("/api/projects/" + state.pid + "/items", { method: "POST", json });
    f.item.value = ""; f.estimated.value = ""; f.actual.value = ""; $("#addError").textContent = "";
    await refresh(); toast("Cost added.");
  } catch (x) { $("#addError").textContent = x.message; }
};

$("#uploadForm").onsubmit = async e => {
  e.preventDefault();
  const msg = $("#uploadMsg"); msg.textContent = "Uploading…";
  try {
    const r = await api("/api/projects/" + state.pid + "/import", { method: "POST", body: new FormData(e.target) });
    msg.textContent = r.message + (r.warnings.length ? " " + r.warnings.length + " row(s) skipped: " + r.warnings.slice(0, 2).join(" ") : "");
    $("#file").value = ""; await refresh();
  } catch (x) { msg.textContent = "Not imported. " + x.message; }
};

// ---------- tabs ----------
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach(x => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".panel").forEach(p => p.hidden = p.id !== "tab-" + b.dataset.tab);
  if (b.dataset.tab === "charts") renderCharts();
  if (b.dataset.tab === "pert") renderPertTable();
});

// ---------- charts ----------
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function renderCharts() {
  if (!state.data || $("#tab-charts").hidden) return;
  const d = state.data.charts, ink = css("--ink"), grid = css("--line");
  Chart.defaults.color = css("--muted"); Chart.defaults.font.family = css("--font");
  for (const k in state.charts) state.charts[k].destroy();
  const money0 = v => cur() + " " + Math.round(v).toLocaleString("en-US");
  const noData = !d.by_category.labels.length;
  $("#chartNote").textContent = noData ? "Enter actual costs to see the charts." :
    "Chart 1 compares only items that have an actual cost. Chart 2 shows the ten items with the largest % variance.";
  state.charts.c1 = new Chart($("#ch1"), { type: "bar",
    data: { labels: d.by_category.labels, datasets: [
      { label: "Estimated", data: d.by_category.estimated, backgroundColor: css("--line") },
      { label: "Actual", data: d.by_category.actual, backgroundColor: css("--accent") }] },
    options: { indexAxis: "y", maintainAspectRatio: false, scales: { x: { grid: { color: grid }, ticks: { callback: v => v.toLocaleString("en-US") } }, y: { grid: { display: false } } },
      plugins: { tooltip: { callbacks: { label: c => c.dataset.label + ": " + money0(c.parsed.x) } } } } });
  state.charts.c2 = new Chart($("#ch2"), { type: "bar",
    data: { labels: d.top_variance.labels, datasets: [{ label: "% variance", data: d.top_variance.pct,
      backgroundColor: d.top_variance.pct.map(v => v > 0 ? css("--over") : css("--under")) }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => pct(c.parsed.x) } } }, scales: { x: { grid: { color: grid }, ticks: { callback: v => v + "%" } }, y: { grid: { display: false }, ticks: { font: { size: 10 } } } } } });
  state.charts.c3 = new Chart($("#ch3"), { type: "doughnut",
    data: { labels: d.actual_by_type.labels, datasets: [{ data: d.actual_by_type.actual, borderColor: css("--panel"),
      backgroundColor: [css("--accent"), "#7d93b2", "#b9c4d4", "#d9dde3"] }] },
    options: { maintainAspectRatio: false, plugins: { tooltip: { callbacks: { label: c => c.label + ": " + money0(c.parsed) } } } } });
}

// ---------- AI ----------
$("#runAi").onclick = async () => {
  const out = $("#aiOut"); out.replaceChildren(h("p", { class: "muted" }, "Analysing…"));
  try {
    const a = await api("/api/projects/" + state.pid + "/ai");
    const kids = a.summary.map(t => h("p", {}, t));
    if (a.flags.length) {
      kids.push(h("h2", {}, "Unusual expenses"));
      kids.push(h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Item", "Estimated", "Actual", "%", "Why it was flagged"].map((t, i) => h("th", { class: i > 0 && i < 4 ? "num" : "" }, t)))),
        h("tbody", {}, a.flags.map(f => h("tr", {}, h("td", {}, f.item), h("td", { class: "num" }, money(f.estimated)), h("td", { class: "num" }, money(f.actual)),
          h("td", { class: "num " + cls(f.pct) }, pct(f.pct)), h("td", { class: "flag-reason" }, f.reasons.join(" "))))))));
    }
    kids.push(h("p", { class: "muted small" }, "Method: " + a.method));
    out.replaceChildren(...kids);
  } catch (x) { out.replaceChildren(h("p", { class: "error" }, x.message)); }
};

// ---------- PERT ----------
function renderPertTable() {
  if (!state.data) return;
  const body = $("#pertTable tbody");
  const rows = state.data.rows;
  if (!rows.length) {
    body.replaceChildren(h("tr", {}, h("td", { colspan: 6, class: "muted" }, "No cost items. Add costs in the Cost data tab first.")));
    return;
  }
  body.replaceChildren(...rows.map(r => {
    const oIn = h("input", { class: "pert-o", type: "number", min: "0", step: "any", value: r.optimistic != null ? r.optimistic : "", placeholder: "min cost", "aria-label": "Optimistic cost for " + r.item });
    const pIn = h("input", { class: "pert-p", type: "number", min: "0", step: "any", value: r.pessimistic != null ? r.pessimistic : "", placeholder: "max cost", "aria-label": "Pessimistic cost for " + r.item });
    const saveBtn = h("button", { class: "xbtn", onclick: async () => {
      const o = oIn.value === "" ? null : oIn.value;
      const p = pIn.value === "" ? null : pIn.value;
      try {
        await api("/api/projects/" + state.pid + "/items/" + r.id + "/pert", { method: "PUT", json: { optimistic: o, pessimistic: p } });
        toast("PERT values saved for " + r.item);
      } catch (x) { toast(x.message); }
    } }, "Save");
    return h("tr", {},
      h("td", {}, r.item), h("td", { class: "cat" }, r.category),
      h("td", { class: "num" }, oIn), h("td", { class: "num" }, money(r.estimated)),
      h("td", { class: "num" }, pIn), h("td", {}, saveBtn));
  }));
}

$("#runPert").onclick = async () => {
  const out = $("#pertOut"); out.replaceChildren(h("p", { class: "muted" }, "Running PERT analysis…"));
  try {
    const r = await api("/api/projects/" + state.pid + "/pert");
    const kids = [];
    const s = r.summary;
    if (s.total_items === 0) {
      kids.push(h("p", { class: "muted" }, "No items have Optimistic and Pessimistic values. Enter them in the table above and save, then run the analysis."));
    } else {
      // Risk badge
      const riskColor = s.risk_level === "Low" ? "under" : s.risk_level === "High" ? "over" : "warn";
      kids.push(h("div", { class: "kpis", style: "margin-bottom:1rem" },
        h("div", {}, h("span", { class: "k" }, "PERT Expected Total"), h("strong", {}, money(s.total_expected))),
        h("div", {}, h("span", { class: "k" }, "Std. Deviation (σ)"), h("strong", {}, money(s.total_std_dev))),
        h("div", {}, h("span", { class: "k" }, "95% Confidence Range"), h("strong", {}, money(s.range_low) + " – " + money(s.range_high))),
        h("div", {}, h("span", { class: "k" }, "Risk Level"), h("strong", {}, h("span", { class: "badge " + riskColor }, s.risk_level))),
        h("div", {}, h("span", { class: "k" }, "Items Analysed"), h("strong", {}, s.total_items + (s.skipped_items ? " (" + s.skipped_items + " skipped)" : "")))
      ));
      kids.push(h("p", {}, s.risk_note));
      // Per-item table
      if (r.rows.length) {
        kids.push(h("h2", { style: "margin-top:1rem" }, "Per-Item PERT Results"));
        kids.push(h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Item", "Category", "Optimistic", "Most Likely", "Pessimistic", "Expected", "Std Dev", "95% Range"].map(
            (t, i) => h("th", { class: i > 1 ? "num" : "" }, t)))),
          h("tbody", {}, r.rows.map(row => h("tr", {},
            h("td", {}, row.item), h("td", { class: "cat" }, row.category),
            h("td", { class: "num" }, money(row.optimistic)), h("td", { class: "num" }, money(row.most_likely)),
            h("td", { class: "num" }, money(row.pessimistic)), h("td", { class: "num" }, money(row.expected)),
            h("td", { class: "num" }, money(row.std_dev)),
            h("td", { class: "num" }, money(row.range_low) + " – " + money(row.range_high))
          )))
        )));
      }
      kids.push(h("p", { class: "muted small", style: "margin-top:.8rem" }, "Formula: Expected = (O + 4M + P) / 6, σ = (P − O) / 6. 95% confidence = Expected ± 2σ. CV = σ / Expected × 100."));
    }
    out.replaceChildren(...kids);
  } catch (x) { out.replaceChildren(h("p", { class: "error" }, x.message)); }
};

// ---------- Compare Files ----------
$("#compareForm").onsubmit = async e => {
  e.preventDefault();
  const msg = $("#compareMsg"); msg.textContent = "Comparing files…";
  const out = $("#compareOut"); out.replaceChildren();
  const form = new FormData(e.target);
  // Remove empty file entries (File 3 optional)
  const cleaned = new FormData();
  for (const f of form.getAll("files")) {
    if (f && f.name) cleaned.append("files", f);
  }
  if ([...cleaned.getAll("files")].length < 2) { msg.textContent = "Upload at least 2 files."; return; }
  try {
    const r = await api("/api/compare", { method: "POST", body: cleaned });
    msg.textContent = "Comparison complete.";
    const kids = [];
    // Summary cards
    kids.push(h("div", { class: "kpis", style: "margin-bottom:1rem" },
      h("div", {}, h("span", { class: "k" }, "Unique Items"), h("strong", {}, String(r.stats.total_unique_items))),
      h("div", {}, h("span", { class: "k" }, "Common to All"), h("strong", {}, String(r.stats.common_items))),
      h("div", {}, h("span", { class: "k" }, "In One File Only"), h("strong", {}, String(r.stats.unique_to_one_file))),
      h("div", {}, h("span", { class: "k" }, "Partial Overlap"), h("strong", {}, String(r.stats.partial_overlap))),
      h("div", {}, h("span", { class: "k" }, "Files Compared"), h("strong", {}, String(r.file_names.length)))
    ));
    // Per-file summaries
    kids.push(h("h2", { style: "margin-top:1rem" }, "File Summaries"));
    kids.push(h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["File", "Items", "Total Estimated", "Total Actual", "Items with Actual"].map(
        (t, i) => h("th", { class: i > 1 ? "num" : "" }, t)))),
      h("tbody", {}, r.file_summaries.map(fs => h("tr", {},
        h("td", {}, fs.name), h("td", { class: "num" }, String(fs.total_items)),
        h("td", { class: "num" }, money(fs.total_estimated)), h("td", { class: "num" }, money(fs.total_actual)),
        h("td", { class: "num" }, String(fs.items_with_actual))
      )))
    )));
    // Deltas
    if (r.deltas.length) {
      kids.push(h("h2", { style: "margin-top:1rem" }, "Changes Between Files"));
      r.deltas.forEach(d => {
        const estCls = d.estimated_delta > 0 ? "over" : d.estimated_delta < 0 ? "under" : "";
        kids.push(h("p", {},
          h("strong", {}, d.from_file), " → ", h("strong", {}, d.to_file), ": ",
          "Estimated ", h("span", { class: estCls }, (d.estimated_delta > 0 ? "+" : "") + money(d.estimated_delta)),
          ", Items " + (d.items_delta > 0 ? "+" : "") + d.items_delta
        ));
      });
    }
    // Detailed comparison table
    kids.push(h("h2", { style: "margin-top:1rem" }, "Item-by-Item Comparison"));
    const hdr = ["Item", "Category", "Status"];
    r.file_names.forEach(n => { hdr.push(n + " Est."); hdr.push(n + " Act."); });
    if (r.file_names.length >= 2) { hdr.push("Est. Δ"); }
    kids.push(h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, hdr.map((t, i) => h("th", { class: i > 1 ? "num" : "" }, t)))),
      h("tbody", {}, r.rows.map(row => {
        const statusBadge = row.status === "common" ? h("span", { class: "badge under" }, "Common") :
          row.status === "unique" ? h("span", { class: "badge warn" }, "Unique") :
          h("span", { class: "badge" }, "Partial");
        const cells = [h("td", {}, row.item), h("td", { class: "cat" }, row.category), h("td", {}, statusBadge)];
        row.files.forEach(fd => {
          cells.push(h("td", { class: "num" }, fd.estimated != null ? money(fd.estimated) : "—"));
          cells.push(h("td", { class: "num" }, fd.actual != null ? money(fd.actual) : "—"));
        });
        if (row.diffs.length) {
          const d = row.diffs[0];
          if (d.estimated_diff != null) {
            const c = d.estimated_diff > 0 ? "over" : d.estimated_diff < 0 ? "under" : "";
            cells.push(h("td", { class: "num " + c }, signed(d.estimated_diff)));
          } else {
            cells.push(h("td", { class: "num" }, "—"));
          }
        }
        return h("tr", {}, ...cells);
      }))
    )));
    out.replaceChildren(...kids);
  } catch (x) { msg.textContent = "Error: " + x.message; }
};

// ---------- start ----------
(async () => {
  try {
    const me = await api("/api/me");
    state.csrf = me.csrf; state.user = me.user;
    if (me.user) await enterApp(); else showAuth();
  } catch (x) { showAuth(); $("#authError").textContent = "Cannot reach the server."; }
})();

