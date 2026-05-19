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

const WEIGHT_META = [
  { key: "preferred_country",    sign: "+", label: "Preferred country",
    desc: "Job's country is in your wishlist." },
  { key: "avoid_country",        sign: "−", label: "Avoided country",
    desc: "Country is blacklisted. Heavy penalty by default." },
  { key: "good_salary",          sign: "+", label: "Good salary",
    desc: "Affordability ratio ≥ min (default 1.5×), or PPP-USD above floor." },
  { key: "low_salary",           sign: "−", label: "Low salary",
    desc: "Affordability < 1× local cost, or PPP-USD below floor." },
  { key: "keyword_match",        sign: "+", label: "Keyword match (×N, max 3)",
    desc: "Each must-have keyword in title/description (capped at 3 hits)." },
  { key: "keyword_avoid",        sign: "−", label: "Avoid keyword present",
    desc: "Description contains any avoid-keyword. Flat penalty, doesn't stack." },
  { key: "preferred_experiment", sign: "+", label: "Preferred experiment",
    desc: "Job lists one of your preferred experiments (STAR, ATLAS, …)." },
  { key: "short_deadline",       sign: "−", label: "Short deadline",
    desc: "Less than min_days_to_deadline (default 10) days to apply." },
];

const LIST_META = [
  { key: "preferred_countries",   label: "Preferred countries",   placeholder: "US, DE, CH …" },
  { key: "avoid_countries",       label: "Avoided countries",     placeholder: "(none)" },
  { key: "keywords_must_have",    label: "Must-have keywords",    placeholder: "jet, QCD, heavy ion …" },
  { key: "keywords_avoid",        label: "Avoid keywords",        placeholder: "dark matter direct detection, …" },
  { key: "preferred_experiments", label: "Preferred experiments", placeholder: "ATLAS, CMS, STAR …" },
];

const STATE = {
  jobs: [],
  sortKey: "score",
  sortDir: "desc",
  expanded: new Set(),
  defaultWeights: {},
  weights: {},
  defaultLists: {},
  lists: {},
  defaultThresholds: {},
  thresholds: {},
  configSummary: {},
};

async function init() {
  try {
    const res = await fetch("digest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STATE.jobs = data.jobs || [];
    STATE.defaultWeights = data.default_weights || {};
    const cs = data.config_summary || {};
    STATE.configSummary = cs;
    STATE.defaultLists = {
      preferred_countries:   (cs.preferred_countries   || []).slice(),
      avoid_countries:       (cs.avoid_countries       || []).slice(),
      keywords_must_have:    (cs.keywords_must_have    || []).slice(),
      keywords_avoid:        (cs.keywords_avoid        || []).slice(),
      preferred_experiments: (cs.preferred_experiments || []).slice(),
    };
    STATE.defaultThresholds = {
      min_affordability_ratio: cs.min_affordability_ratio != null ? cs.min_affordability_ratio : 1.5,
      min_days_to_deadline:    cs.min_days_to_deadline    != null ? cs.min_days_to_deadline    : 10,
    };
    const saved = loadPrefs() || {};
    STATE.weights    = saved.weights    || Object.assign({}, STATE.defaultWeights);
    STATE.lists      = saved.lists      || JSON.parse(JSON.stringify(STATE.defaultLists));
    STATE.thresholds = saved.thresholds || Object.assign({}, STATE.defaultThresholds);

    renderHeader(data);
    populateSourceFilter(data.per_source || {});
    populateCountryFilter(STATE.jobs);
    buildScoringPanel();
    wireFilters();
    wireSorting();
    recomputeScores();
    render();
  } catch (err) {
    document.getElementById("jobs-body").innerHTML =
      `<tr><td colspan="7" class="loading">Failed to load digest.json: ${escapeHtml(err.message)}</td></tr>`;
  }
}

const PREFS_KEY = "hep-jobs-prefs-v2";

function loadPrefs() {
  try {
    const s = localStorage.getItem(PREFS_KEY);
    return s ? JSON.parse(s) : null;
  } catch { return null; }
}
function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      weights: STATE.weights,
      lists: STATE.lists,
      thresholds: STATE.thresholds,
    }));
  } catch {}
}

function parseList(raw) {
  return raw.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
}

function listToText(items) {
  return (items || []).join(", ");
}

function hasKeyword(text, kw) {
  if (!text || !kw) return false;
  const k = kw.toLowerCase();
  if (k.includes(" ") || k.includes("-")) return text.includes(k);
  return new RegExp(`\\b${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(text);
}

function buildScoringPanel() {
  buildWeightsList();
  buildListsEditor();
  buildThresholdsEditor();
  document.getElementById("reset-weights").addEventListener("click", () => {
    STATE.weights    = { ...STATE.defaultWeights };
    STATE.lists      = JSON.parse(JSON.stringify(STATE.defaultLists));
    STATE.thresholds = { ...STATE.defaultThresholds };
    try { localStorage.removeItem(PREFS_KEY); } catch {}
    buildWeightsList();
    buildListsEditor();
    buildThresholdsEditor();
    recomputeScores();
    render();
  });
}

function buildWeightsList() {
  const list = document.getElementById("weights-list");
  list.innerHTML = "";
  for (const m of WEIGHT_META) {
    const v = (STATE.weights[m.key] != null ? STATE.weights[m.key]
              : (STATE.defaultWeights[m.key] != null ? STATE.defaultWeights[m.key] : 0));
    list.insertAdjacentHTML("beforeend", `
      <li class="weight-row ${m.sign === '+' ? 'pos' : 'neg'}">
        <div class="w-head">
          <span class="w-sign">${m.sign}</span>
          <span class="w-label">${escapeHtml(m.label)}</span>
          <output class="w-value" data-out="${m.key}">${v}</output>
        </div>
        <input type="range" class="w-slider" data-weight="${m.key}" min="0" max="50" value="${v}">
        <div class="w-desc">${escapeHtml(m.desc)}</div>
      </li>
    `);
  }
  list.querySelectorAll(".w-slider").forEach(s => {
    s.addEventListener("input", () => {
      const k = s.dataset.weight;
      STATE.weights[k] = Number(s.value);
      document.querySelector(`[data-out="${k}"]`).textContent = s.value;
      savePrefs();
      recomputeScores();
      render();
    });
  });
}

function buildListsEditor() {
  const root = document.getElementById("lists-editor");
  root.innerHTML = "";
  for (const m of LIST_META) {
    const items = STATE.lists[m.key] || [];
    const text = listToText(items);
    root.insertAdjacentHTML("beforeend", `
      <div class="list-row">
        <label class="list-label" for="list-${m.key}">${escapeHtml(m.label)}</label>
        <textarea id="list-${m.key}" class="list-input" data-list="${m.key}"
          placeholder="${escapeHtml(m.placeholder)}" rows="2">${escapeHtml(text)}</textarea>
        <div class="list-chips" data-chips="${m.key}">${chipsHtml(items)}</div>
      </div>
    `);
  }
  root.querySelectorAll("textarea.list-input").forEach(ta => {
    ta.addEventListener("input", () => {
      const k = ta.dataset.list;
      const items = parseList(ta.value);
      STATE.lists[k] = items;
      document.querySelector(`[data-chips="${k}"]`).innerHTML = chipsHtml(items);
      savePrefs();
      recomputeScores();
      render();
    });
  });
}

function chipsHtml(items) {
  return (items || []).map(it => `<span class="chip">${escapeHtml(it)}</span>`).join("");
}

function buildThresholdsEditor() {
  const root = document.getElementById("thresholds-editor");
  const t = STATE.thresholds;
  root.innerHTML = `
    <label class="threshold-row">
      Min affordability ratio for "good salary" bonus:
      <input type="number" data-threshold="min_affordability_ratio" step="0.1" min="0" max="5" value="${t.min_affordability_ratio}">
    </label>
    <label class="threshold-row">
      "Short deadline" if days-to-deadline less than:
      <input type="number" data-threshold="min_days_to_deadline" step="1" min="0" max="365" value="${t.min_days_to_deadline}">
    </label>
  `;
  root.querySelectorAll("input[data-threshold]").forEach(inp => {
    inp.addEventListener("input", () => {
      STATE.thresholds[inp.dataset.threshold] = Number(inp.value);
      savePrefs();
      recomputeScores();
      render();
    });
  });
}

function recomputeScores() {
  const w = STATE.weights;
  const prefC  = new Set((STATE.lists.preferred_countries   || []).map(s => s.toUpperCase()));
  const avoidC = new Set((STATE.lists.avoid_countries       || []).map(s => s.toUpperCase()));
  const mustKw = STATE.lists.keywords_must_have || [];
  const avoidKw = STATE.lists.keywords_avoid    || [];
  const prefExp = new Set((STATE.lists.preferred_experiments || []).map(s => s.toLowerCase()));
  const minAfford = Number(STATE.thresholds.min_affordability_ratio != null ? STATE.thresholds.min_affordability_ratio : 1.5);
  const minDays   = Number(STATE.thresholds.min_days_to_deadline    != null ? STATE.thresholds.min_days_to_deadline    : 10);

  for (const j of STATE.jobs) {
    const countries = (j.countries && j.countries.length ? j.countries : (j.country ? [j.country] : []))
                       .map(c => (c || "").toUpperCase());
    const text = j.match_text || "";

    let s = 50;
    const matchedKw = mustKw.filter(k => hasKeyword(text, k)).slice(0, 5);
    const matchedAvoid = avoidKw.filter(k => hasKeyword(text, k));
    const kwCount = Math.min(matchedKw.length, 3);
    const expMatch = (j.experiments || []).some(e => prefExp.has((e || "").toLowerCase()));

    let goodSal = false, lowSal = false;
    if (j.affordability_low != null) {
      if (j.affordability_low >= minAfford) goodSal = true;
      else if (j.affordability_high != null && j.affordability_high < 1.0) lowSal = true;
    }
    const dl = j.days_to_deadline;
    const shortDl = (dl != null && dl < minDays);

    if (countries.some(c => prefC.has(c)))  s += (w.preferred_country     || 0);
    if (countries.some(c => avoidC.has(c))) s -= (w.avoid_country         || 0);
    if (goodSal)                            s += (w.good_salary           || 0);
    if (lowSal)                             s -= (w.low_salary            || 0);
    if (kwCount)                            s += (w.keyword_match         || 0) * kwCount;
    if (matchedAvoid.length)                s -= (w.keyword_avoid         || 0);
    if (expMatch)                           s += (w.preferred_experiment  || 0);
    if (shortDl)                            s -= (w.short_deadline        || 0);

    s = Math.max(0, Math.min(100, Math.round(s)));
    j.score = s;
    j.bucket = bucketFor(s);

    j._reasons_live = [];
    if (countries.some(c => prefC.has(c)))   j._reasons_live.push("preferred country");
    if (countries.some(c => avoidC.has(c)))  j._reasons_live.push("avoided country");
    if (goodSal && j.affordability_low != null)
                                              j._reasons_live.push(`affordability ${j.affordability_low.toFixed(2)}× ≥ ${minAfford}`);
    if (lowSal)                              j._reasons_live.push("affordability < 1× local cost");
    if (matchedKw.length)                    j._reasons_live.push(`keywords: ${matchedKw.join(", ")}`);
    if (matchedAvoid.length)                 j._reasons_live.push(`avoid keywords: ${matchedAvoid.join(", ")}`);
    if (expMatch)                            j._reasons_live.push("preferred experiment");
    if (shortDl)                             j._reasons_live.push(`only ${dl} days to deadline`);
  }
}

function bucketFor(s) {
  if (s >= 80) return "urgent";
  if (s >= 65) return "apply";
  if (s >= 50) return "consider";
  return "skim";
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
    `${data.jobs.length} ranked · ${data.n_total != null ? data.n_total : "?"} scanned`;
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
  const aff = formatAffordability(j);
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
      <td class="affordability">${aff}</td>
    </tr>`;
}

function formatAffordability(j) {
  if (j.affordability_low == null || j.affordability_high == null) {
    return '<span class="muted">—</span>';
  }
  const lab = j.affordability_label ? `<span class="label">${escapeHtml(j.affordability_label)}</span>` : "";
  return `${j.affordability_low.toFixed(2)}×–${j.affordability_high.toFixed(2)}×${lab}`;
}

function formatSalaryColumn(j) {
  const gL = j.salary_ppp_usd_low, gH = j.salary_ppp_usd_high;
  const nL = j.salary_net_ppp_usd_low, nH = j.salary_net_ppp_usd_high;
  if (gL && gH) {
    const gross = `$${monthly(gL)}–${monthly(gH)}/mo`;
    const net = (nL && nH) ? `~$${monthly(nL)}–${monthly(nH)} net/mo` : "";
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

function monthly(annual) {
  if (annual == null) return "";
  const m = annual / 12;
  if (m >= 10000) return Math.round(m / 100) / 10 + "k";
  if (m >= 1000)  return (Math.round(m / 100) / 10).toFixed(1) + "k";
  return Math.round(m).toString();
}

function detailRowHtml(j) {
  const sal = formatSalary(j);
  const ppp = (j.salary_ppp_usd_low && j.salary_ppp_usd_high)
    ? `~$${fmt(j.salary_ppp_usd_low / 12)}–${fmt(j.salary_ppp_usd_high / 12)} PPP-USD/month gross` : "";
  const pppNet = (j.salary_net_ppp_usd_low && j.salary_net_ppp_usd_high)
    ? `~$${fmt(j.salary_net_ppp_usd_low / 12)}–${fmt(j.salary_net_ppp_usd_high / 12)} PPP-USD/month net (after tax)` : "";
  const reasonsArr = (j._reasons_live && j._reasons_live.length) ? j._reasons_live : (j.reasons || []);
  const reasons = reasonsArr.map(r => `<li>${escapeHtml(r)}</li>`).join("");
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
      <td colspan="7">
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
  return `${fmt(j.salary_low_local / 12)}–${fmt(j.salary_high_local / 12)} ${escapeHtml(j.salary_currency)}/month`;
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
