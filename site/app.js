"use strict";

const FLAG = {
  US: "🇺🇸", DE: "🇩🇪", CH: "🇨🇭", NL: "🇳🇱", GB: "🇬🇧", FR: "🇫🇷",
  IT: "🇮🇹", CZ: "🇨🇿", JP: "🇯🇵", CN: "🇨🇳", KR: "🇰🇷", IL: "🇮🇱",
  CA: "🇨🇦", SE: "🇸🇪", NO: "🇳🇴", DK: "🇩🇰", ES: "🇪🇸", PL: "🇵🇱",
  AT: "🇦🇹", BE: "🇧🇪", FI: "🇫🇮", IN: "🇮🇳", RU: "🇷🇺",
};

const BUCKET_LABEL = {
  urgent: "🔥 Urgent",
  apply: "✅ Worth applying",
  consider: "🤔 Consider",
  skim: "👀 Maybe",
};

const STATE = {
  jobs: [],
  sortKey: "score",
  sortDir: "desc",
  expanded: new Set(),
};

async function init() {
  try {
    const res = await fetch("digest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STATE.jobs = data.jobs || [];
    renderHeader(data);
    populateSourceFilter(data.per_source || {});
    populateCountryFilter(STATE.jobs);
    wireFilters();
    wireSorting();
    render();
  } catch (err) {
    document.getElementById("jobs-body").innerHTML =
      `<tr><td colspan="6" class="loading">Failed to load digest.json: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderHeader(data) {
  const updated = data.generated_at
    ? new Date(data.generated_at).toLocaleString(undefined, {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", timeZoneName: "short",
      })
    : "unknown";
  document.getElementById("updated-at").textContent = `updated ${updated}`;
  document.getElementById("counts").textContent =
    `${data.jobs.length} ranked · ${data.n_total ?? "?"} scanned`;
}

function populateSourceFilter(perSource) {
  const sources = [...new Set(STATE.jobs.map(j => j.source))].sort();
  const root = document.getElementById("source-filter");
  for (const src of sources) {
    const n = STATE.jobs.filter(j => j.source === src).length;
    const id = `src-${src}`;
    root.insertAdjacentHTML("beforeend",
      `<label><input type="checkbox" class="source-cb" value="${escapeHtml(src)}" checked> ${escapeHtml(src)} <span class="muted">(${n})</span></label>`);
  }
}

function populateCountryFilter(jobs) {
  const counts = {};
  for (const j of jobs) {
    const c = j.country || "—";
    counts[c] = (counts[c] || 0) + 1;
  }
  const root = document.getElementById("country-filter");
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  for (const [c, n] of entries) {
    const flag = FLAG[c] || "";
    root.insertAdjacentHTML("beforeend",
      `<label><input type="checkbox" class="country-cb" value="${escapeHtml(c)}" checked> ${flag} ${escapeHtml(c)} <span class="muted">(${n})</span></label>`);
  }
}

function wireFilters() {
  document.getElementById("search").addEventListener("input", render);
  document.getElementById("min-score").addEventListener("input", e => {
    document.getElementById("min-score-val").textContent = e.target.value;
    render();
  });
  document.querySelectorAll(".bucket-filter, .source-cb, .country-cb")
    .forEach(cb => cb.addEventListener("change", render));
}

function wireSorting() {
  document.querySelectorAll("th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (STATE.sortKey === key) {
        STATE.sortDir = STATE.sortDir === "desc" ? "asc" : "desc";
      } else {
        STATE.sortKey = key;
        STATE.sortDir = key === "score" ? "desc" : "asc";
      }
      document.querySelectorAll("th.sortable").forEach(t => {
        t.classList.toggle("active", t.dataset.sort === STATE.sortKey);
        t.classList.toggle("asc", t.dataset.sort === STATE.sortKey && STATE.sortDir === "asc");
        t.classList.toggle("desc", t.dataset.sort === STATE.sortKey && STATE.sortDir === "desc");
      });
      render();
    });
  });
}

function getActiveFilters() {
  const search = document.getElementById("search").value.trim().toLowerCase();
  const minScore = Number(document.getElementById("min-score").value);
  const buckets = new Set(
    [...document.querySelectorAll(".bucket-filter:checked")].map(cb => cb.value)
  );
  const sources = new Set(
    [...document.querySelectorAll(".source-cb:checked")].map(cb => cb.value)
  );
  const countries = new Set(
    [...document.querySelectorAll(".country-cb:checked")].map(cb => cb.value)
  );
  return { search, minScore, buckets, sources, countries };
}

function applyFilters(jobs, f) {
  return jobs.filter(j => {
    if (j.score < f.minScore) return false;
    if (!f.buckets.has(j.bucket)) return false;
    if (!f.sources.has(j.source)) return false;
    if (!f.countries.has(j.country || "—")) return false;
    if (f.search) {
      const hay = `${j.title} ${(j.institutions || []).join(" ")}`.toLowerCase();
      if (!hay.includes(f.search)) return false;
    }
    return true;
  });
}

function applySort(jobs) {
  const { sortKey, sortDir } = STATE;
  const dir = sortDir === "asc" ? 1 : -1;
  return [...jobs].sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (sortKey === "deadline") {
      av = av || "9999-12-31";
      bv = bv || "9999-12-31";
    }
    if (av == null) return 1;
    if (bv == null) return -1;
    if (av < bv) return -1 * dir;
    if (av > bv) return  1 * dir;
    return 0;
  });
}

function render() {
  const filtered = applySort(applyFilters(STATE.jobs, getActiveFilters()));
  const body = document.getElementById("jobs-body");
  const empty = document.getElementById("empty-state");
  body.innerHTML = "";
  empty.classList.toggle("hidden", filtered.length > 0);
  for (const j of filtered) {
    body.insertAdjacentHTML("beforeend", rowHtml(j));
    if (STATE.expanded.has(j.url)) {
      body.insertAdjacentHTML("beforeend", detailRowHtml(j));
    }
  }
  body.querySelectorAll("tr.job-row").forEach(tr => {
    tr.addEventListener("click", () => toggleExpand(tr.dataset.url));
  });
}

function toggleExpand(url) {
  if (STATE.expanded.has(url)) STATE.expanded.delete(url);
  else STATE.expanded.add(url);
  render();
}

function rowHtml(j) {
  const flag = FLAG[j.country] || "";
  const dl = formatDeadline(j);
  const sal = formatSalaryColumn(j);
  return `
    <tr class="job-row" data-url="${escapeHtml(j.url)}">
      <td><span class="score-badge ${j.bucket}">${j.score}</span></td>
      <td>
        <span class="job-title">${escapeHtml(j.title)}</span>
        ${j.institutions && j.institutions.length
          ? `<span class="institutions">${escapeHtml(j.institutions.slice(0, 2).join(" · "))}</span>`
          : ""}
      </td>
      <td><span class="source-tag">${escapeHtml(j.source)}</span></td>
      <td><span class="country-flag">${flag}</span>${escapeHtml(j.country || "—")}</td>
      <td>${dl}</td>
      <td class="salary">${sal}</td>
    </tr>`;
}

function formatSalaryColumn(j) {
  const gL = j.salary_ppp_usd_low, gH = j.salary_ppp_usd_high;
  const nL = j.salary_net_ppp_usd_low, nH = j.salary_net_ppp_usd_high;
  if (gL && gH) {
    const gross = `$${kfmt(gL)}–${kfmt(gH)}`;
    const net = (nL && nH) ? `~$${kfmt(nL)}–${kfmt(nH)} net` : "";
    const aff = j.affordability_label
      ? `<span class="label">${escapeHtml(j.affordability_label)}</span>`
      : "";
    return `<span class="gross">${gross}</span>${net ? `<span class="net">${net}</span>` : ""}${aff}`;
  }
  if (j.salary_estimate) {
    return `<span class="estimate">${escapeHtml(j.salary_estimate)}</span>`;
  }
  return '<span class="muted">—</span>';
}

function kfmt(n) {
  if (n == null) return "";
  return Math.round(n / 1000) + "k";
}

function detailRowHtml(j) {
  const sal = formatSalary(j);
  const ppp = (j.salary_ppp_usd_low && j.salary_ppp_usd_high)
    ? `~$${fmt(j.salary_ppp_usd_low)}–${fmt(j.salary_ppp_usd_high)} PPP-USD gross` : "";
  const pppNet = (j.salary_net_ppp_usd_low && j.salary_net_ppp_usd_high)
    ? `~$${fmt(j.salary_net_ppp_usd_low)}–${fmt(j.salary_net_ppp_usd_high)} PPP-USD net (after tax)` : "";
  const reasons = (j.reasons || []).map(r => `<li>${escapeHtml(r)}</li>`).join("");
  const dl = [
    j.ranks && j.ranks.length ? dlItem("Rank", j.ranks.join(", ")) : "",
    j.experiments && j.experiments.length ? dlItem("Experiments", j.experiments.slice(0, 5).join(", ")) : "",
    j.fields_of_interest && j.fields_of_interest.length ? dlItem("Fields", j.fields_of_interest.join(", ")) : "",
    sal ? dlItem("Typical salary", `${sal}${ppp ? `<br><span class="muted">${ppp}</span>` : ""}${pppNet ? `<br><span class="muted">${pppNet}</span>` : ""}${j.salary_source ? `<br><span class="muted">scale: ${escapeHtml(j.salary_source)}</span>` : ""}`) : "",
    j.salary_estimate ? dlItem("LLM salary estimate", escapeHtml(j.salary_estimate)) : "",
    j.affordability_low ? dlItem("Affordability", `${j.affordability_low.toFixed(2)}×–${j.affordability_high.toFixed(2)}× <span class="muted">(${escapeHtml(j.affordability_label || "")})</span>`) : "",
    j.col_index ? dlItem("Cost-of-living", `${j.col_index} <span class="muted">(NYC=100)</span>`) : "",
    j.llm_summary ? dlItem("LLM summary", escapeHtml(j.llm_summary)) : "",
    j.salary_mentioned_in_post ? dlItem("Mentioned in posting", escapeHtml(j.salary_mentioned_in_post.slice(0, 240))) : "",
    j.posted ? dlItem("Posted", escapeHtml(j.posted.slice(0, 10))) : "",
  ].filter(Boolean).join("");

  return `
    <tr class="detail-row">
      <td colspan="6">
        ${reasons ? `<ul class="reasons">${reasons}</ul>` : ""}
        <dl class="detail-grid">${dl}</dl>
        ${j.description_short
          ? `<div class="description">${escapeHtml(j.description_short)}</div>` : ""}
        <a class="open-link" href="${escapeHtml(j.url)}" target="_blank" rel="noopener">Open posting →</a>
      </td>
    </tr>`;
}

function dlItem(label, value) {
  return `<div><dt>${label}</dt><dd>${value}</dd></div>`;
}

function formatDeadline(j) {
  if (!j.deadline) return '<span class="muted">—</span>';
  const days = j.days_to_deadline;
  let cls = "";
  if (days != null) {
    if (days <= 14) cls = "deadline-soon";
    else if (days <= 30) cls = "deadline-mid";
  }
  const suffix = days != null ? ` <span class="muted">(${days}d)</span>` : "";
  return `<span class="${cls}">${escapeHtml(j.deadline)}</span>${suffix}`;
}

function formatSalary(j) {
  if (!j.salary_low_local || !j.salary_high_local || !j.salary_currency) return "";
  return `${fmt(j.salary_low_local)}–${fmt(j.salary_high_local)} ${escapeHtml(j.salary_currency)}`;
}


function fmt(n) {
  if (n == null) return "";
  return Math.round(n).toLocaleString();
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

init();
