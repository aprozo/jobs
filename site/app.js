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

// Each entry merges a weight with the list or threshold that activates it.
const CRITERIA = [
  { key: "preferred_country",    sign: "+", label: "Preferred country",
    desc: "Bonus when the job's country is in this list.",
    list: "preferred_countries", listPlaceholder: "US, DE, CH …" },
  { key: "good_salary",          sign: "+", label: "Good salary",
    desc: "Bonus when local-cost-adjusted affordability meets the threshold.",
    threshold: "min_affordability_ratio",
    thresholdLabel: "trigger if affordability ≥",
    thresholdUnit: "×", thresholdStep: 0.1, thresholdMin: 0, thresholdMax: 5 },
  { key: "keyword_match",        sign: "+", label: "Keyword match",
    desc: "Each must-have keyword in title/description, capped at 3 hits.",
    list: "keywords_must_have", listPlaceholder: "jet, QCD, heavy ion …" },
  { key: "keyword_avoid",        sign: "−", label: "Avoid keywords",
    desc: "Penalty if any avoid-keyword appears (flat — doesn't stack).",
    list: "keywords_avoid", listPlaceholder: "dark matter direct detection …" },
  { key: "preferred_experiment", sign: "+", label: "Preferred experiment",
    desc: "Bonus when the job lists one of these experiments.",
    list: "preferred_experiments", listPlaceholder: "ATLAS, CMS, STAR …" },
];

const STATE = {
  jobs: [],
  sortKey: "score",
  sortDir: "desc",
  jobsByUrl: {},
  defaultWeights: {},
  weights: {},
  defaultLists: {},
  lists: {},
  defaultThresholds: {},
  thresholds: {},
  configSummary: {},
  siteMeta: {},
  // Active filter state (driven by stat tiles + chips + search + min-score).
  activeBuckets: new Set(["urgent", "apply", "consider", "skim"]),
  activeSources: new Set(),
  activeCountries: new Set(),
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
    STATE.siteMeta = data.site_meta || {};
    STATE.defaultLists = {
      preferred_countries:   (cs.preferred_countries   || []).slice(),
      keywords_must_have:    (cs.keywords_must_have    || []).slice(),
      keywords_avoid:        (cs.keywords_avoid        || []).slice(),
      preferred_experiments: (cs.preferred_experiments || []).slice(),
    };
    STATE.defaultThresholds = {
      min_affordability_ratio: cs.min_affordability_ratio != null ? cs.min_affordability_ratio : 1.5,
    };
    const saved = loadPrefs() || {};
    STATE.weights    = saved.weights    || Object.assign({}, STATE.defaultWeights);
    STATE.lists      = saved.lists      || JSON.parse(JSON.stringify(STATE.defaultLists));
    STATE.thresholds = saved.thresholds || Object.assign({}, STATE.defaultThresholds);

    // Drop keys from saved prefs that no longer exist in the current schema
    // (e.g. removed penalty fields) and clamp weights to the slider range.
    for (const k of Object.keys(STATE.weights)) {
      if (!(k in STATE.defaultWeights)) { delete STATE.weights[k]; continue; }
      STATE.weights[k] = Math.max(0, Math.min(20, Number(STATE.weights[k]) || 0));
    }
    for (const k of Object.keys(STATE.defaultWeights)) {
      if (STATE.weights[k] == null) STATE.weights[k] = STATE.defaultWeights[k];
    }
    for (const k of Object.keys(STATE.lists)) {
      if (!(k in STATE.defaultLists)) delete STATE.lists[k];
    }
    for (const k of Object.keys(STATE.thresholds)) {
      if (!(k in STATE.defaultThresholds)) delete STATE.thresholds[k];
    }
    savePrefs();

    STATE.jobsByUrl = {};
    for (const j of STATE.jobs) STATE.jobsByUrl[j.url] = j;

    renderHeader(data);
    populateStatTiles();
    populateSourceFilter(data.per_source || {});
    populateCountryFilter(STATE.jobs);
    buildScoringPanel();
    wireFilters();
    wireSorting();
    wireStatTiles();
    wireSubscribe();
    wireScoringToggle();
    wireModal();
    recomputeScores();
    render();
  } catch (err) {
    document.getElementById("jobs-body").innerHTML =
      `<tr><td colspan="6" class="loading">Failed to load digest.json: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function wireScoringToggle() {
  const btn = document.getElementById("open-scoring");
  const panel = document.getElementById("scoring-panel");
  if (btn && panel) {
    btn.addEventListener("click", () => {
      panel.open = !panel.open;
      if (panel.open) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

function populateStatTiles() {
  // Counts based on raw bucket assignment from current weights — refreshed on rescore.
  refreshStatTiles();
}

function refreshStatTiles() {
  const counts = { urgent: 0, apply: 0, consider: 0, skim: 0 };
  for (const j of STATE.jobs) counts[j.bucket] = (counts[j.bucket] || 0) + 1;
  for (const li of document.querySelectorAll(".stat-tiles .stat")) {
    const b = li.dataset.bucket;
    li.querySelector(".n").textContent = counts[b] || 0;
    li.classList.toggle("off", !STATE.activeBuckets.has(b));
  }
}

function wireStatTiles() {
  document.querySelectorAll(".stat-tiles .stat").forEach(li => {
    li.addEventListener("click", () => {
      const b = li.dataset.bucket;
      if (STATE.activeBuckets.has(b)) STATE.activeBuckets.delete(b);
      else STATE.activeBuckets.add(b);
      li.classList.toggle("off", !STATE.activeBuckets.has(b));
      render();
    });
  });
}

function wireSubscribe() {
  const form = document.getElementById("subscribe-form");
  const input = document.getElementById("subscribe-email");
  const status = document.getElementById("subscribe-status");
  const endpoint = STATE.siteMeta.subscribe_url || "";
  const hint = document.getElementById("subscribe-endpoint-hint");
  if (endpoint && hint) hint.textContent = "Formspree (configured)";

  if (!endpoint) {
    status.textContent = "Subscribe endpoint not configured — see config.yaml.";
    status.className = "sub-status err";
    input.disabled = true;
    form.querySelector("button").disabled = true;
    return;
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const email = (input.value || "").trim();
    if (!email) return;
    const btn = form.querySelector("button");
    btn.disabled = true;
    status.textContent = "Sending…";
    status.className = "sub-status";
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          source: "aprozo.github.io/jobs subscribe form",
          message: "Please add me to the HEP jobs daily digest.",
        }),
      });
      if (res.ok) {
        status.textContent = "Thanks — you'll be added to the digest list.";
        status.className = "sub-status ok";
        input.value = "";
      } else {
        const body = await res.text();
        status.textContent = "Couldn't subscribe: " + (body.slice(0, 80) || res.status);
        status.className = "sub-status err";
      }
    } catch (e) {
      status.textContent = "Network error — try again later.";
      status.className = "sub-status err";
    } finally {
      btn.disabled = false;
    }
  });
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
  buildCriteriaList();
  document.getElementById("reset-weights").addEventListener("click", () => {
    STATE.weights    = Object.assign({}, STATE.defaultWeights);
    STATE.lists      = JSON.parse(JSON.stringify(STATE.defaultLists));
    STATE.thresholds = Object.assign({}, STATE.defaultThresholds);
    try { localStorage.removeItem(PREFS_KEY); } catch {}
    buildCriteriaList();
    recomputeScores();
    render();
  });
}

function chipsHtml(items) {
  return (items || []).map(it => `<span class="chip">${escapeHtml(it)}</span>`).join("");
}

function buildCriteriaList() {
  const root = document.getElementById("criteria-list");
  root.innerHTML = "";
  for (const m of CRITERIA) {
    const w = (STATE.weights[m.key] != null ? STATE.weights[m.key]
              : (STATE.defaultWeights[m.key] != null ? STATE.defaultWeights[m.key] : 0));
    const items = m.list ? (STATE.lists[m.list] || []) : null;
    const thr = m.threshold ? (STATE.thresholds[m.threshold] != null
                                ? STATE.thresholds[m.threshold]
                                : STATE.defaultThresholds[m.threshold]) : null;
    let body = "";
    if (m.list) {
      body = `
        <div class="c-detail">
          <textarea class="list-input" data-list="${m.list}"
            placeholder="${escapeHtml(m.listPlaceholder)}" rows="2">${escapeHtml(listToText(items))}</textarea>
          <div class="list-chips" data-chips="${m.list}">${chipsHtml(items)}</div>
        </div>`;
    } else if (m.threshold) {
      body = `
        <div class="c-detail c-threshold">
          <span class="t-label">${escapeHtml(m.thresholdLabel)}</span>
          <input type="number" class="t-input" data-threshold="${m.threshold}"
            step="${m.thresholdStep}" min="${m.thresholdMin}" max="${m.thresholdMax}" value="${thr}">
          <span class="t-unit">${escapeHtml(m.thresholdUnit)}</span>
        </div>`;
    } else {
      body = `<div class="c-detail c-static">No configurable input.</div>`;
    }
    root.insertAdjacentHTML("beforeend", `
      <li class="criterion-card ${m.sign === '+' ? 'pos' : 'neg'}">
        <div class="c-head">
          <span class="c-sign">${m.sign}</span>
          <span class="c-label">${escapeHtml(m.label)}</span>
          <span class="c-weight-wrap">
            <span class="c-weight-name">weight</span>
            <output class="c-weight" data-out="${m.key}">${w}</output>
          </span>
        </div>
        <input type="range" class="c-slider" data-weight="${m.key}" min="0" max="20" value="${w}">
        <div class="c-desc">${escapeHtml(m.desc)}</div>
        ${body}
      </li>
    `);
  }

  root.querySelectorAll(".c-slider").forEach(s => {
    s.addEventListener("input", () => {
      const k = s.dataset.weight;
      STATE.weights[k] = Number(s.value);
      const out = document.querySelector(`[data-out="${k}"]`);
      if (out) out.textContent = s.value;
      savePrefs();
      recomputeScores();
      render();
    });
  });
  root.querySelectorAll("textarea.list-input").forEach(ta => {
    ta.addEventListener("input", () => {
      const k = ta.dataset.list;
      const items = parseList(ta.value);
      STATE.lists[k] = items;
      const c = document.querySelector(`[data-chips="${k}"]`);
      if (c) c.innerHTML = chipsHtml(items);
      savePrefs();
      recomputeScores();
      render();
    });
  });
  root.querySelectorAll("input.t-input").forEach(inp => {
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
  const mustKw = STATE.lists.keywords_must_have || [];
  const avoidKw = STATE.lists.keywords_avoid    || [];
  const prefExp = new Set((STATE.lists.preferred_experiments || []).map(s => s.toLowerCase()));
  const minAfford = Number(STATE.thresholds.min_affordability_ratio != null ? STATE.thresholds.min_affordability_ratio : 1.5);

  for (const j of STATE.jobs) {
    const countries = (j.countries && j.countries.length ? j.countries : (j.country ? [j.country] : []))
                       .map(c => (c || "").toUpperCase());
    const text = j.match_text || "";

    let s = 50;
    const matchedKw = mustKw.filter(k => hasKeyword(text, k)).slice(0, 5);
    const matchedAvoid = avoidKw.filter(k => hasKeyword(text, k));
    const kwCount = Math.min(matchedKw.length, 3);
    const expMatch = (j.experiments || []).some(e => prefExp.has((e || "").toLowerCase()));

    let goodSal = false;
    if (j.affordability_low != null && j.affordability_low >= minAfford) goodSal = true;

    if (countries.some(c => prefC.has(c)))  s += (w.preferred_country     || 0);
    if (goodSal)                            s += (w.good_salary           || 0);
    if (kwCount)                            s += (w.keyword_match         || 0) * kwCount;
    if (matchedAvoid.length)                s -= (w.keyword_avoid         || 0);
    if (expMatch)                           s += (w.preferred_experiment  || 0);

    s = Math.max(0, Math.min(100, Math.round(s)));
    j.score = s;
    j.bucket = bucketFor(s);

    j._reasons_live = [];
    if (countries.some(c => prefC.has(c)))   j._reasons_live.push("preferred country");
    if (goodSal && j.affordability_low != null)
                                              j._reasons_live.push(`affordability ${j.affordability_low.toFixed(2)}× ≥ ${minAfford}`);
    if (matchedKw.length)                    j._reasons_live.push(`keywords: ${matchedKw.join(", ")}`);
    if (matchedAvoid.length)                 j._reasons_live.push(`avoid keywords: ${matchedAvoid.join(", ")}`);
    if (expMatch)                            j._reasons_live.push("preferred experiment");
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
  const sources = Array.from(new Set(STATE.jobs.map(j => j.source))).sort();
  STATE.activeSources = new Set(sources);
  const root = document.getElementById("source-filter");
  // Keep legend already in HTML
  for (const src of sources) {
    const n = STATE.jobs.filter(j => j.source === src).length;
    root.insertAdjacentHTML("beforeend",
      `<label class="on" data-source="${escapeHtml(src)}"><input type="checkbox" class="source-cb" value="${escapeHtml(src)}" checked hidden>${escapeHtml(src)}<span class="muted">${n}</span></label>`);
  }
  root.querySelectorAll("label[data-source]").forEach(lbl => {
    lbl.addEventListener("click", (e) => {
      e.preventDefault();
      const src = lbl.dataset.source;
      if (STATE.activeSources.has(src)) STATE.activeSources.delete(src);
      else STATE.activeSources.add(src);
      lbl.classList.toggle("on", STATE.activeSources.has(src));
      render();
    });
  });
}

function populateCountryFilter(jobs) {
  const counts = {};
  for (const j of jobs) {
    const c = j.country || "—";
    counts[c] = (counts[c] || 0) + 1;
  }
  // Empty selection = no filter (show all). User clicks to whitelist countries.
  STATE.activeCountries = new Set();
  const root = document.getElementById("country-filter");
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  for (const [c, n] of entries) {
    const flag = FLAG[c] || "";
    root.insertAdjacentHTML("beforeend",
      `<label data-country="${escapeHtml(c)}">${flag} ${escapeHtml(c)} <span class="muted">${n}</span></label>`);
  }
  root.querySelectorAll("label[data-country]").forEach(lbl => {
    lbl.addEventListener("click", (e) => {
      e.preventDefault();
      const c = lbl.dataset.country;
      if (STATE.activeCountries.has(c)) STATE.activeCountries.delete(c);
      else STATE.activeCountries.add(c);
      lbl.classList.toggle("on", STATE.activeCountries.has(c));
      render();
    });
  });
}

function wireFilters() {
  document.getElementById("search").addEventListener("input", render);
  document.getElementById("min-score").addEventListener("input", e => {
    document.getElementById("min-score-val").textContent = e.target.value;
    render();
  });
  const rb = document.getElementById("reset-filters");
  if (rb) rb.addEventListener("click", resetAllFilters);
}

function resetAllFilters() {
  document.getElementById("search").value = "";
  const ms = document.getElementById("min-score");
  ms.value = 0;
  document.getElementById("min-score-val").textContent = "0";

  STATE.activeBuckets = new Set(["urgent", "apply", "consider", "skim"]);
  for (const li of document.querySelectorAll(".stat-tiles .stat")) {
    li.classList.remove("off");
  }

  document.querySelectorAll("#source-filter label[data-source]").forEach(lbl => {
    STATE.activeSources.add(lbl.dataset.source);
    lbl.classList.add("on");
  });
  STATE.activeCountries.clear();
  document.querySelectorAll("#country-filter label[data-country]").forEach(lbl => {
    lbl.classList.remove("on");
  });
  render();
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
  return {
    search,
    minScore,
    buckets:   STATE.activeBuckets,
    sources:   STATE.activeSources,
    countries: STATE.activeCountries,
  };
}

function applyFilters(jobs, f) {
  return jobs.filter(j => {
    if (j.score < f.minScore) return false;
    if (!f.buckets.has(j.bucket)) return false;
    if (!f.sources.has(j.source)) return false;
    if (f.countries.size > 0 && !f.countries.has(j.country || "—")) return false;
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
  refreshStatTiles();
  const filtered = applySort(applyFilters(STATE.jobs, getActiveFilters()));
  const body = document.getElementById("jobs-body");
  const empty = document.getElementById("empty-state");
  body.innerHTML = "";
  empty.classList.toggle("hidden", filtered.length > 0);
  for (const j of filtered) {
    body.insertAdjacentHTML("beforeend", rowHtml(j));
  }
  body.querySelectorAll("tr.job-row").forEach(tr => {
    tr.addEventListener("click", () => openModal(tr.dataset.url));
  });
}

function rowHtml(j) {
  const flag = FLAG[j.country] || "";
  const dl = formatDeadline(j);
  const sal = formatSalaryColumn(j);
  const aff = formatAffordability(j);
  const cc = j.country || "—";
  const place = j.city
    ? `<span class="city">${escapeHtml(j.city)}</span>`
    : `<span class="country-code muted">${escapeHtml(cc)}</span>`;
  const also = (j.also_sources && j.also_sources.length)
    ? `<span class="also-sources" title="Also listed in">${
        j.also_sources.map(s => `<span class="src-tag">${escapeHtml(s)}</span>`).join("")
      }</span>` : "";
  return `
    <tr class="job-row" data-url="${escapeHtml(j.url)}">
      <td><span class="score-badge ${j.bucket}">${j.score}</span></td>
      <td>
        <span class="job-title">${escapeHtml(j.title)}</span>${also}
        ${j.institutions && j.institutions.length
          ? `<span class="institutions">${escapeHtml(j.institutions.slice(0, 2).join(" · "))}</span>`
          : ""}
      </td>
      <td class="where"><span class="country-flag" title="${escapeHtml(cc)}">${flag || cc}</span>${place}</td>
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

// Spot FX (USD per 1 unit of local). Kept in sync with salary_data.py.
const USD_FX_PER_UNIT = {
  USD: 1.00, EUR: 1.08, GBP: 1.27, CHF: 1.12, JPY: 0.0067, CAD: 0.74,
  SEK: 0.095, DKK: 0.144, NOK: 0.094, CNY: 0.14, CZK: 0.044,
  PLN: 0.25, ILS: 0.27, KRW: 0.00073, INR: 0.012,
};
// PPP-USD per 1 unit of local (OECD PPP, approx). In sync with salary_data.py.
const PPP_USD_PER_UNIT = {
  USD: 1.00, EUR: 1.30, GBP: 1.50, CHF: 0.95, JPY: 0.0095, CAD: 0.85,
  SEK: 0.115, DKK: 0.135, NOK: 0.105, CNY: 0.22, CZK: 0.055,
  PLN: 0.32, ILS: 0.32, KRW: 0.001, INR: 0.029,
};
// Effective single-postdoc gross→net tax rate by ISO country. Subset of
// salary_data.EFFECTIVE_TAX_RATE — enough to net-estimate posted salaries.
const EFFECTIVE_TAX_RATE = {
  US: 0.27, DE: 0.34, FR: 0.25, GB: 0.23, CH: 0.13, IT: 0.31, NL: 0.30,
  ES: 0.23, SE: 0.32, DK: 0.36, NO: 0.30, JP: 0.20, CN: 0.15, CA: 0.27,
  IL: 0.22, IN: 0.10, KR: 0.18, AT: 0.32, BE: 0.34, PL: 0.25, CZ: 0.22,
};

function _parseNumberWithSuffix(s) {
  s = s.replace(/\s+/g, "").replace(/,/g, "");
  const mult = /[kK]$/.test(s) ? 1e3 : /[mM]$/.test(s) ? 1e6 : 1;
  return parseFloat(s.replace(/[kKmM]$/, "")) * mult;
}

// If the posting has an explicit pay figure, return
// {display, currency, amount, period: 'yr'|'mo'} or null.
function extractPrecisePay(text) {
  if (!text) return null;
  let t = text.split("LLM:").pop().trim();
  if (!t) return null;
  if (/(\$|€|£|¥|[A-Z]{3})?\s*\d[\d.,]*\s*[kKmM]?\s*[-–]\s*\$?\d/.test(t)) return null;
  if (/\bto\b/i.test(t) && /\d/.test(t)) return null;
  if (/^(TV-?L|TV[öo]D|TV-V|UC|NIH|NRSA|CNRS|UKRI|STFC|INFN)[\s\w]*$/i.test(t)) return null;

  const SYM = { "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY" };
  let cur = null, numStr = null;
  let m = t.match(/(USD|EUR|GBP|CHF|JPY|CNY|CAD|SEK|NOK|DKK|PLN|CZK|KRW|INR|ILS|\$|€|£|¥)\s*([\d.,]+\s*[kKmM]?)/);
  if (m) { cur = SYM[m[1]] || m[1]; numStr = m[2]; }
  else {
    m = t.match(/([\d.,]+\s*[kKmM]?)\s*(USD|EUR|GBP|CHF|JPY|CNY|CAD|SEK|NOK|DKK|PLN|CZK|KRW|INR|ILS)/);
    if (m) { cur = m[2]; numStr = m[1]; }
  }
  if (!cur || !numStr) return null;
  const amount = _parseNumberWithSuffix(numStr);
  if (!isFinite(amount) || amount <= 0) return null;

  // Determine period: explicit > heuristic on USD-equivalent.
  let period = null;
  const per = /per\s*(month|annum|year)|\/\s*(mo|month|year|yr|annum)/i.exec(t);
  if (per) period = /month|mo/i.test(per[0]) ? "mo" : "yr";
  if (!period) {
    const fx = USD_FX_PER_UNIT[cur] || 1;
    period = amount * fx >= 10_000 ? "yr" : "mo";
  }
  const display = `${cur} ${numStr.replace(/\s+/g, "").trim()}${period === "mo" ? "/mo" : "/yr"}`;
  return { display, currency: cur, amount, period };
}

function postedUsdMonthly(p) {
  if (!p) return null;
  const fx = USD_FX_PER_UNIT[p.currency];
  if (!fx) return null;
  const annualUsd = (p.period === "mo" ? p.amount * 12 : p.amount) * fx;
  return annualUsd / 12;
}

// US benchmark cost-of-living indices (NYC = 100). Used to project the job's
// salary's purchasing power into US cities the reader can mentally compare to.
const US_CITY_COL = {
  "New York":      100,
  "San Francisco": 105,
  "Boston":         90,
  "Stanford":      100,
  "Princeton":      80,
  "Los Angeles":    80,
  "Chicago":        72,
  "Houston":        65,
  "Atlanta":        65,
};

function usCityEquivalents(j) {
  const localCol = j.col_index;
  if (!localCol) return null;
  // Pick best monthly-USD basis: posting-precise > scaled-range midpoint.
  let monthlyUsd = null;
  const precise = extractPrecisePay(j.salary_mentioned_in_post);
  if (precise) monthlyUsd = postedUsdMonthly(precise);
  if (monthlyUsd == null && j.salary_usd_low && j.salary_usd_high) {
    monthlyUsd = ((j.salary_usd_low + j.salary_usd_high) / 2) / 12;
  }
  if (monthlyUsd == null) return null;
  return Object.entries(US_CITY_COL).map(([city, idx]) => ({
    city,
    monthly: monthlyUsd * (idx / localCol),
    index: idx,
  }));
}

function buildUsEquivTable(j) {
  const rows = usCityEquivalents(j);
  if (!rows) return "";
  const trs = rows
    .sort((a, b) => b.monthly - a.monthly)
    .map(r => `<tr><th>${escapeHtml(r.city)} <span class="muted">(COL ${r.index})</span></th>` +
              `<td>$${Math.round(r.monthly).toLocaleString()}/mo</td></tr>`)
    .join("");
  const note = `<p class="us-equiv-note muted">If you moved this job's pay to a US city, ` +
               `you'd need roughly the above to feel the same lifestyle ` +
               `(scaled by cost-of-living vs the job's local city, NYC = 100).</p>`;
  return `<h4 class="modal-subhead">Purchasing-power equivalent in US cities</h4>` +
         `<table class="modal-salary-table us-equiv-table">` +
         `<thead><tr><th>City</th><th>Per month</th></tr></thead>` +
         `<tbody>${trs}</tbody></table>${note}`;
}

function formatSalaryColumn(j) {
  const precise = extractPrecisePay(j.salary_mentioned_in_post);
  if (precise) {
    const usdM = postedUsdMonthly(precise);
    const conv = (usdM != null) ? `<span class="net">≈ $${monthly(usdM * 12)}/mo USD</span>` : "";
    return `<span class="gross posted">${escapeHtml(precise.display)}</span>${conv}<span class="net muted">posted</span>`;
  }
  const usdL = j.salary_usd_low, usdH = j.salary_usd_high;
  const pppL = j.salary_ppp_usd_low, pppH = j.salary_ppp_usd_high;
  const netL = j.salary_net_usd_low, netH = j.salary_net_usd_high;
  if (usdL && usdH) {
    const nominal = `$${monthly(usdL)}–${monthly(usdH)}/mo`;
    const net = (netL && netH) ? `~$${monthly(netL)}–${monthly(netH)} net/mo` : "";
    const ppp = (pppL && pppH) ? `PPP $${monthly(pppL)}–${monthly(pppH)}/mo` : "";
    return `<span class="gross">${nominal}</span>${net ? `<span class="net">${net}</span>` : ""}${ppp ? `<span class="ppp">${ppp}</span>` : ""}`;
  }
  if (pppL && pppH) {
    const gross = `PPP $${monthly(pppL)}–${monthly(pppH)}/mo`;
    return `<span class="gross">${gross}</span>`;
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

function dlItem(label, value) {
  return `<div><dt>${label}</dt><dd>${value}</dd></div>`;
}

function buildSalaryTable(j) {
  const precise = extractPrecisePay(j.salary_mentioned_in_post);
  const rowsHtml = [];

  function pushSingle(label, monthly, cur) {
    if (monthly == null || !isFinite(monthly)) return;
    rowsHtml.push(
      `<tr><th>${escapeHtml(label)}</th>` +
      `<td>${cur} ${Math.round(monthly).toLocaleString()}/mo</td></tr>`
    );
  }
  function pushRange(label, low, high, cur, isMonthly) {
    if (low == null || high == null) return;
    const lo = isMonthly ? Math.round(low / 12) : Math.round(low);
    const hi = isMonthly ? Math.round(high / 12) : Math.round(high);
    rowsHtml.push(
      `<tr><th>${escapeHtml(label)}</th>` +
      `<td>${cur} ${lo.toLocaleString()}–${hi.toLocaleString()}/mo</td></tr>`
    );
  }

  if (precise) {
    // Precise pay from posting: derive every other row from this single
    // figure (no scaled ranges, since the user wants exact numbers).
    const annual = precise.period === "mo" ? precise.amount * 12 : precise.amount;
    const cur = precise.currency;
    const fx = USD_FX_PER_UNIT[cur];
    const ppp = PPP_USD_PER_UNIT[cur];
    const tax = EFFECTIVE_TAX_RATE[j.country] || null;
    const isCERN = (j.institutions || []).some(i => /CERN/i.test(i));
    const netFactor = isCERN ? 1 : (tax != null ? (1 - tax) : null);

    const usdM = postedUsdMonthly(precise);
    rowsHtml.push(
      `<tr class="posted-row"><th>From posting</th>` +
      `<td><b>${escapeHtml(precise.display)}</b>` +
      (usdM != null ? ` <span class="muted">·</span> ≈ $${Math.round(usdM).toLocaleString()}/mo USD` : "") +
      `</td></tr>`
    );
    pushSingle("Gross (local)",        annual / 12, cur);
    if (fx)  pushSingle("Gross (nominal USD)", annual * fx / 12, "$");
    if (ppp) pushSingle("Gross (PPP-USD)",     annual * ppp / 12, "$");
    if (netFactor != null) {
      const netAnnual = annual * netFactor;
      pushSingle("Net (local)",          netAnnual / 12, cur);
      if (fx)  pushSingle("Net (nominal USD)", netAnnual * fx / 12, "$");
      if (ppp) pushSingle("Net (PPP-USD)",     netAnnual * ppp / 12, "$");
    }
  } else {
    pushRange("Gross (local)",        j.salary_low_local,        j.salary_high_local,        j.salary_currency || "", true);
    pushRange("Gross (nominal USD)",  j.salary_usd_low,          j.salary_usd_high,          "$", true);
    pushRange("Gross (PPP-USD)",      j.salary_ppp_usd_low,      j.salary_ppp_usd_high,      "$", true);
    pushRange("Net (local)",          j.salary_net_local_low,    j.salary_net_local_high,    j.salary_currency || "", true);
    pushRange("Net (nominal USD)",    j.salary_net_usd_low,      j.salary_net_usd_high,      "$", true);
    pushRange("Net (PPP-USD)",        j.salary_net_ppp_usd_low,  j.salary_net_ppp_usd_high,  "$", true);
  }

  if (!rowsHtml.length) return "";
  const heading = precise
    ? `<h4 class="modal-subhead">Salary breakdown — from posting</h4>`
    : `<h4 class="modal-subhead">Salary breakdown — typical scale</h4>`;
  return `${heading}<table class="modal-salary-table">
    <thead><tr><th>Type</th><th>Per month</th></tr></thead>
    <tbody>${rowsHtml.join("")}</tbody>
  </table>`;
}

function wireModal() {
  const modal = document.getElementById("job-modal");
  if (!modal) return;
  modal.querySelectorAll("[data-close]").forEach(el => {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });
}

function openModal(url) {
  const j = STATE.jobsByUrl[url];
  const modal = document.getElementById("job-modal");
  if (!j || !modal) return;
  document.getElementById("modal-title").textContent = j.title || "";

  const inst = (j.institutions && j.institutions.length)
    ? j.institutions.slice(0, 4).join(" · ") : "";
  const where = [j.city, j.country].filter(Boolean).join(", ");
  document.getElementById("modal-institution").innerHTML =
    [escapeHtml(inst), where ? `<span class="muted"> — ${escapeHtml(where)}</span>` : ""].join("");

  const metaLi = [];
  metaLi.push(`<li><b>Score</b> ${j.score} (${escapeHtml(j.bucket || "")})</li>`);
  metaLi.push(`<li><b>Source</b> ${escapeHtml(j.source)}</li>`);
  if (j.also_sources && j.also_sources.length) {
    metaLi.push(`<li><b>Also in</b> ${j.also_sources.map(escapeHtml).join(", ")}</li>`);
  }
  if (j.deadline) {
    const days = j.days_to_deadline;
    const tail = (days != null) ? (days < 0 ? " (closed)" : ` (${days}d)`) : "";
    metaLi.push(`<li><b>Deadline</b> ${escapeHtml(j.deadline)}${tail}</li>`);
  }
  if (j.posted) metaLi.push(`<li><b>Posted</b> ${escapeHtml(j.posted.slice(0, 10))}</li>`);
  if (j.ranks && j.ranks.length) metaLi.push(`<li><b>Rank</b> ${escapeHtml(j.ranks.join(", "))}</li>`);
  if (j.experiments && j.experiments.length)
    metaLi.push(`<li><b>Experiments</b> ${escapeHtml(j.experiments.slice(0, 5).join(", "))}</li>`);
  if (j.fields_of_interest && j.fields_of_interest.length)
    metaLi.push(`<li><b>Fields</b> ${escapeHtml(j.fields_of_interest.join(", "))}</li>`);
  document.getElementById("modal-meta").innerHTML = metaLi.join("");

  const reasonsArr = (j._reasons_live && j._reasons_live.length) ? j._reasons_live : (j.reasons || []);
  document.getElementById("modal-reasons").innerHTML =
    reasonsArr.map(r => `<li>${escapeHtml(r)}</li>`).join("");

  const dl = [
    (j.affordability_low != null && j.affordability_high != null)
      ? dlItem("Affordability",
          `${j.affordability_low.toFixed(2)}×–${j.affordability_high.toFixed(2)}× <span class="muted">(${escapeHtml(j.affordability_label || "")})</span>`) : "",
    j.col_index ? dlItem("Cost-of-living", `${j.col_index} <span class="muted">(NYC=100)</span>`) : "",
    j.llm_summary ? dlItem("LLM summary", escapeHtml(j.llm_summary)) : "",
    j.salary_mentioned_in_post ? dlItem("Mentioned in posting", escapeHtml(j.salary_mentioned_in_post.slice(0, 400))) : "",
  ].filter(Boolean).join("");
  document.getElementById("modal-detail").innerHTML = dl;
  document.getElementById("modal-salary").innerHTML = buildSalaryTable(j);

  document.getElementById("modal-description").textContent = j.description_short || "";

  const link = document.getElementById("modal-link");
  link.href = j.url;
  link.textContent = "Open posting →";

  const alt = document.getElementById("modal-alt-urls");
  if (j.alt_urls && j.alt_urls.length) {
    alt.innerHTML = "Alt: " + j.alt_urls.map(u =>
      `<a href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(shortHost(u))}</a>`
    ).join(" ");
  } else {
    alt.innerHTML = "";
  }

  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function closeModal() {
  const modal = document.getElementById("job-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

function shortHost(u) {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; }
}

function formatDeadline(j) {
  if (!j.deadline) return '<span class="muted">—</span>';
  const days = j.days_to_deadline;
  let cls = "";
  let suffix = "";
  if (days != null) {
    if (days < 0)       { cls = "deadline-past"; suffix = ` <span class="muted">(closed)</span>`; }
    else if (days <= 14){ cls = "deadline-soon"; suffix = ` <span class="muted">(${days}d)</span>`; }
    else if (days <= 30){ cls = "deadline-mid";  suffix = ` <span class="muted">(${days}d)</span>`; }
    else                 { suffix = ` <span class="muted">(${days}d)</span>`; }
  }
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
