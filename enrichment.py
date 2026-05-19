"""
enrichment.py — Attach salary / cost-of-living / LLM-extracted metadata
to a Job.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import time
import threading
from typing import Any

from salary_data import (
    POSTDOC_SCALES, PPP_USD_PER_UNIT, COL_INDEX_BY_CITY, COL_INDEX_BY_COUNTRY,
    to_ppp_usd, to_usd, affordability_ratio, net_after_tax,
)

log = logging.getLogger("hep-jobs.enrich")


@dataclasses.dataclass
class JobEnrichment:
    country: str | None
    city: str | None
    salary_low_local: float | None
    salary_high_local: float | None
    salary_currency: str | None
    salary_source: str | None
    salary_ppp_usd_low: float | None
    salary_ppp_usd_high: float | None
    affordability_low: float | None      # salary_low / local annual cost basket
    affordability_high: float | None
    col_index: int | None                # NYC = 100
    salary_mentioned_in_post: str | None
    llm_summary: str | None
    salary_estimate: str | None = None   # LLM/manual estimate incl. benefits
    salary_net_local_low: float | None = None
    salary_net_local_high: float | None = None
    salary_net_ppp_usd_low: float | None = None
    salary_net_ppp_usd_high: float | None = None
    salary_usd_low: float | None = None         # nominal spot-FX gross
    salary_usd_high: float | None = None
    salary_net_usd_low: float | None = None     # nominal spot-FX net
    salary_net_usd_high: float | None = None


# ----------- regex-based extractors -------------------------------------------

_SALARY_PATTERNS = [
    # very rough heuristics; refined later with LLM if available
    re.compile(r"(?i)(?:salary|stipend|gross|net)[^.]{0,80}?(?:€|EUR|USD|\$|CHF|GBP|£|¥|JPY|CAD|CZK|PLN|SEK|DKK)[^.]{0,40}"),
    re.compile(r"(?i)(?:€|\$|£|CHF|EUR|USD|GBP)\s?[\d.,]{2,12}\b[^.]{0,30}"),
]

_CITY_RE = re.compile(
    r"\b(Zurich|Geneva|Lausanne|New York|Boston|Chicago|Princeton|Berkeley|Stanford|"
    r"London|Oxford|Cambridge|Edinburgh|Paris|Lyon|Marseille|"
    r"Munich|Berlin|Hamburg|Heidelberg|"
    r"Amsterdam|Utrecht|Copenhagen|Stockholm|Oslo|"
    r"Rome|Milan|Bologna|Madrid|Barcelona|"
    r"Tokyo|Osaka|Beijing|Shanghai|Seoul|"
    r"Tel Aviv|Jerusalem|Toronto|Vancouver|"
    r"Prague|Warsaw|Krakow|Vienna|Mumbai|Bangalore)\b"
)


def _find_salary_mentions(text: str) -> str | None:
    snippets = []
    for pat in _SALARY_PATTERNS:
        for m in pat.finditer(text):
            snippets.append(m.group(0).strip())
            if len(snippets) >= 3:
                break
    return " | ".join(snippets) if snippets else None


def _find_city(text: str) -> str | None:
    m = _CITY_RE.search(text)
    return m.group(0) if m else None


# ----------- optional LLM enrichment ------------------------------------------

LLM_PROMPT = """\
You are an assistant helping a high-energy physics postdoc evaluate a job posting.

Given the job description below, return a compact JSON object with these keys:
  - "salary_text": any explicit salary, stipend or pay-range mention (verbatim, or "")
  - "duration_years": integer best-guess of contract length, or null
  - "city": primary work location city, or ""
  - "country_iso2": ISO-3166 alpha-2 code, or ""
  - "experiments": list of experiment/collaboration names mentioned
  - "topics": list of physics topics/keywords (e.g. "jet substructure", "EIC", "QGP")
  - "remote_possible": boolean
  - "needs_visa_sponsorship_yes": boolean

Return ONLY the JSON object. No prose.

JOB TITLE: {title}
INSTITUTIONS: {insts}
DESCRIPTION:
{desc}
"""


def llm_extract(job, cfg: dict) -> dict | None:
    llm = cfg.get("llm", {})
    provider = llm.get("provider")
    if not provider:
        return None
    try:
        if provider == "manual":
            return _llm_manual(job, llm)
        prompt = LLM_PROMPT.format(
            title=job.title,
            insts=", ".join(job.institutions[:5]),
            desc=job.description[:6000],
        )
        if provider == "anthropic":
            return _llm_anthropic(prompt, llm)
        if provider == "ollama":
            return _llm_ollama(prompt, llm)
        if provider == "openrouter":
            return _llm_openrouter(prompt, llm)
        if provider == "gemini":
            return _llm_gemini(prompt, llm)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM extraction failed for %s: %s", job.key, e)
    return None


def _llm_manual(job, cfg: dict) -> dict | None:
    """Provider 'manual': read pre-computed extractions from a JSON file
    keyed by job.key (source:source_id). Used for one-shot bulk seeding."""
    path = cfg.get("path", "manual_enrichments.json")
    cache = getattr(_llm_manual, "_cache", None)
    if cache is None:
        try:
            with open(path) as f:
                cache = json.load(f)
        except FileNotFoundError:
            cache = {}
        _llm_manual._cache = cache
    return cache.get(job.key)


def _llm_anthropic(prompt: str, cfg: dict) -> dict | None:
    import anthropic  # type: ignore
    api_key = os.getenv("ANTHROPIC_API_KEY") or cfg.get("api_key")
    if not api_key:
        log.warning("Anthropic API key not set; skipping LLM enrichment")
        return None
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=cfg.get("model", "claude-haiku-4-5-20251001"),
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return _parse_json_loose(text)


def _llm_ollama(prompt: str, cfg: dict) -> dict | None:
    import requests
    url = cfg.get("ollama_url", "http://localhost:11434/api/generate")
    r = requests.post(
        url,
        json={"model": cfg.get("model", "llama3.1:8b"),
              "prompt": prompt,
              "stream": False,
              "format": "json",
              "options": {"temperature": 0}},
        timeout=120,
    )
    r.raise_for_status()
    return _parse_json_loose(r.json().get("response", ""))


_RATE_LOCK = threading.Lock()
_LAST_CALL_AT: dict[str, float] = {}

def _rate_limit(key: str, min_interval_s: float) -> None:
    """Block until at least `min_interval_s` has passed since the last call for `key`."""
    with _RATE_LOCK:
        last = _LAST_CALL_AT.get(key, 0.0)
        wait = (last + min_interval_s) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL_AT[key] = time.monotonic()


def _llm_openrouter(prompt: str, cfg: dict) -> dict | None:
    import requests
    api_key = os.getenv("OPENROUTER_API_KEY") or cfg.get("api_key")
    if not api_key:
        log.warning("OpenRouter API key not set; skipping LLM enrichment")
        return None
    _rate_limit("openrouter", float(cfg.get("min_interval_s", 3.5)))
    r = requests.post(
        cfg.get("openrouter_url", "https://openrouter.ai/api/v1/chat/completions"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": cfg.get("referer", "https://aprozo.github.io/jobs/"),
            "X-Title": cfg.get("app_title", "HEP jobs monitor"),
        },
        json={
            "model": cfg.get("model", "deepseek/deepseek-chat-v3.1:free"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 600,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"]
    return _parse_json_loose(text)


def _llm_gemini(prompt: str, cfg: dict) -> dict | None:
    import requests
    api_key = os.getenv("GEMINI_API_KEY") or cfg.get("api_key")
    if not api_key:
        log.warning("Gemini API key not set; skipping LLM enrichment")
        return None
    _rate_limit("gemini", float(cfg.get("min_interval_s", 4.2)))
    model = cfg.get("model", "gemini-2.0-flash")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    r = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "maxOutputTokens": 600,
            },
        },
        timeout=120,
    )
    r.raise_for_status()
    body = r.json()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_loose(text)


def _parse_json_loose(s: str) -> dict | None:
    s = s.strip()
    # strip code fences if model wrapped output
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # try to find the first {...} block
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


# ----------- main entry point -------------------------------------------------

def enrich_job(job, cfg: dict, conn: sqlite3.Connection) -> JobEnrichment:
    # cache check
    cur = conn.execute("SELECT payload FROM enrich_cache WHERE source=? AND source_id=?",
                       (job.source, job.source_id))
    row = cur.fetchone()
    if row:
        return JobEnrichment(**json.loads(row[0]))

    country = job.countries[0] if job.countries else None
    city = _find_city(job.description) or _find_city(" ".join(job.institutions))
    sal_mention = _find_salary_mentions(job.description)

    llm = llm_extract(job, cfg)
    if llm:
        if llm.get("country_iso2"):
            country = llm["country_iso2"].upper()
        if llm.get("city"):
            city = llm["city"]
        if llm.get("salary_text"):
            sal_mention = (sal_mention or "") + " || LLM: " + llm["salary_text"]

    is_cern = any("CERN" in (i or "") for i in (job.institutions or []))
    scale = POSTDOC_SCALES.get(country) if country else None
    if scale:
        sal_low, sal_high, sal_cur, sal_src = scale
        ppp_low = to_ppp_usd(sal_low, sal_cur)
        ppp_high = to_ppp_usd(sal_high, sal_cur)
        usd_low = to_usd(sal_low, sal_cur)
        usd_high = to_usd(sal_high, sal_cur)
        afford = affordability_ratio(sal_low, sal_high, sal_cur, country)
        afford_low, afford_high = afford if afford else (None, None)
        net_low = net_after_tax(sal_low, country or "", is_cern=is_cern)
        net_high = net_after_tax(sal_high, country or "", is_cern=is_cern)
        net_ppp_low = to_ppp_usd(net_low, sal_cur) if net_low else None
        net_ppp_high = to_ppp_usd(net_high, sal_cur) if net_high else None
        net_usd_low = to_usd(net_low, sal_cur) if net_low else None
        net_usd_high = to_usd(net_high, sal_cur) if net_high else None
    else:
        sal_low = sal_high = None
        sal_cur = None
        sal_src = None
        ppp_low = ppp_high = None
        usd_low = usd_high = None
        afford_low = afford_high = None
        net_low = net_high = None
        net_ppp_low = net_ppp_high = None
        net_usd_low = net_usd_high = None

    col_idx = (COL_INDEX_BY_CITY.get(city) if city else None) \
              or (COL_INDEX_BY_COUNTRY.get(country) if country else None)

    summary = None
    if llm:
        bits = []
        if llm.get("experiments"):
            bits.append("exp=" + ", ".join(llm["experiments"][:4]))
        if llm.get("topics"):
            bits.append("topics=" + ", ".join(llm["topics"][:5]))
        if llm.get("duration_years"):
            bits.append(f"{llm['duration_years']}y")
        if llm.get("remote_possible"):
            bits.append("remote-friendly")
        summary = "; ".join(bits) if bits else None

    enr = JobEnrichment(
        country=country,
        city=city,
        salary_low_local=sal_low,
        salary_high_local=sal_high,
        salary_currency=sal_cur,
        salary_source=sal_src,
        salary_ppp_usd_low=ppp_low,
        salary_ppp_usd_high=ppp_high,
        affordability_low=afford_low,
        affordability_high=afford_high,
        col_index=col_idx,
        salary_mentioned_in_post=sal_mention,
        llm_summary=summary,
        salary_estimate=(llm or {}).get("salary_estimate") if llm else None,
        salary_net_local_low=net_low,
        salary_net_local_high=net_high,
        salary_net_ppp_usd_low=net_ppp_low,
        salary_net_ppp_usd_high=net_ppp_high,
        salary_usd_low=usd_low,
        salary_usd_high=usd_high,
        salary_net_usd_low=net_usd_low,
        salary_net_usd_high=net_usd_high,
    )

    conn.execute(
        "INSERT OR REPLACE INTO enrich_cache(source, source_id, payload, created) "
        "VALUES (?,?,?,?)",
        (job.source, job.source_id, json.dumps(dataclasses.asdict(enr)),
         dt.datetime.utcnow().isoformat()),
    )
    conn.commit()
    return enr
