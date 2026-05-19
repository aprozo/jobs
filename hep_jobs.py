#!/usr/bin/env python3
"""
hep_jobs.py — INSPIRE-HEP job monitor.

Polls https://inspirehep.net/api/jobs with user-configured filters, dedupes
against a SQLite store of previously-seen postings, enriches with
salary/region metadata, scores against user criteria, and emits a digest.

Designed to be run on a cron / systemd timer / GitHub Actions schedule.

INSPIRE API reference: https://github.com/inspirehep/rest-api-doc
Rate limit: 15 requests / 5s per IP. We back off well within that.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

from enrichment import enrich_job, JobEnrichment
from notify import write_markdown_digest, send_email_digest, write_json_digest


log = logging.getLogger("hep-jobs")


# ---------- data model ----------------------------------------------------------------

@dataclasses.dataclass
class Job:
    """Normalized view of a job posting from any source."""
    source: str                     # e.g. "inspire", "ajo", "atlas-rss"
    source_id: str                  # opaque ID unique within the source
    title: str
    institutions: list[str]
    regions: list[str]              # e.g. ["Europe"], ["North America"]
    countries: list[str]            # ISO codes inferred from institutions when possible
    ranks: list[str]                # JUNIOR / POSTDOC / SENIOR / ...
    fields_of_interest: list[str]   # hep-ex, hep-ph, ...
    experiments: list[str]
    description: str                # plain-text (HTML stripped)
    deadline: str | None            # ISO date or None
    posted: str | None              # ISO datetime
    url: str
    raw: dict                       # full metadata for debugging / future fields

    @property
    def key(self) -> str:
        """Stable composite primary key across sources."""
        return f"{self.source}:{self.source_id}"


# ---------- INSPIRE client moved to sources.InspireSource -----------------------------
# _normalize() is kept here because sources.py imports it.


def _normalize(hit: dict) -> Job:
    """Convert one INSPIRE API hit into a Job dataclass."""
    md = hit.get("metadata", {})
    recid = str(md.get("control_number") or hit.get("id"))

    title = md.get("position") or "(untitled)"

    insts = []
    for inst in md.get("institutions", []) or []:
        name = inst.get("value")
        if name:
            insts.append(name)

    regions = list(md.get("regions", []) or [])
    ranks = list(md.get("ranks", []) or [])

    fields = []
    for f in md.get("arxiv_categories", []) or []:
        fields.append(f)
    for f in md.get("inspire_categories", []) or []:
        if isinstance(f, dict):
            term = f.get("term")
            if term:
                fields.append(term)

    experiments = []
    for exp in md.get("accelerator_experiments", []) or []:
        name = exp.get("name") or exp.get("legacy_name")
        if name:
            experiments.append(name)

    description = _strip_html(md.get("description") or "")
    deadline = md.get("deadline_date")
    posted = md.get("publication_date") or hit.get("created")
    url = f"https://inspirehep.net/jobs/{recid}"
    countries = _infer_countries(regions, insts, description)

    return Job(
        source="inspire",
        source_id=recid,
        title=title,
        institutions=insts,
        regions=regions,
        countries=countries,
        ranks=ranks,
        fields_of_interest=fields,
        experiments=experiments,
        description=description,
        deadline=deadline,
        posted=posted,
        url=url,
        raw=md,
    )


_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_COUNTRY_HINTS = {
    # crude inst-name -> ISO map; the salary table also handles regions
    "CERN": "CH", "DESY": "DE", "KEK": "JP", "BNL": "US", "Brookhaven": "US",
    "Fermilab": "US", "SLAC": "US", "LANL": "US", "LBNL": "US", "ORNL": "US",
    "MIT": "US", "Princeton": "US", "Yale": "US", "Stanford": "US", "Berkeley": "US",
    "Harvard": "US", "Caltech": "US", "Chicago": "US", "Michigan": "US",
    "TRIUMF": "CA", "McGill": "CA", "Toronto": "CA",
    "INFN": "IT", "Rome": "IT", "Milan": "IT", "Bologna": "IT", "Padova": "IT",
    "Oxford": "GB", "Cambridge": "GB", "Imperial": "GB", "Manchester": "GB", "UCL": "GB",
    "Edinburgh": "GB", "Glasgow": "GB", "Birmingham": "GB", "Bristol": "GB",
    "Heidelberg": "DE", "Munich": "DE", "Aachen": "DE", "Hamburg": "DE", "Mainz": "DE",
    "Karlsruhe": "DE", "Bonn": "DE", "Wuppertal": "DE", "Göttingen": "DE",
    "Saclay": "FR", "Orsay": "FR", "IN2P3": "FR", "CEA": "FR", "Polytechnique": "FR",
    "Nikhef": "NL", "Amsterdam": "NL", "Nijmegen": "NL",
    "PSI": "CH", "ETH": "CH", "EPFL": "CH", "Geneva": "CH", "Zurich": "CH",
    "RIKEN": "JP", "Tokyo": "JP", "Osaka": "JP",
    "IHEP": "CN", "Beijing": "CN", "Shanghai": "CN", "Tsinghua": "CN",
    "Stockholm": "SE", "Uppsala": "SE", "Lund": "SE",
    "Copenhagen": "DK", "NBI": "DK",
    "Bergen": "NO", "Oslo": "NO",
    "Madrid": "ES", "Barcelona": "ES", "IFAE": "ES", "IFIC": "ES",
    "Warsaw": "PL", "Krakow": "PL", "Cracow": "PL",
    "Prague": "CZ", "Charles University": "CZ",
    "Vienna": "AT", "HEPHY": "AT",
    "Tel Aviv": "IL", "Technion": "IL", "Weizmann": "IL",
    "Seoul": "KR", "KAIST": "KR",
    "Mumbai": "IN", "TIFR": "IN", "Bangalore": "IN",
}

_REGION_TO_COUNTRY = {
    # very approximate: used only when no inst hint matched
    "North America": "US",
    "Europe": None,        # ambiguous; leave unset
    "Asia": None,
    "Other": None,
}

def _infer_countries(regions: list[str], insts: list[str], desc: str) -> list[str]:
    out: list[str] = []
    blob = " ".join(insts + [desc])
    for needle, code in _COUNTRY_HINTS.items():
        if needle in blob and code not in out:
            out.append(code)
    if not out:
        for r in regions:
            c = _REGION_TO_COUNTRY.get(r)
            if c and c not in out:
                out.append(c)
    return out


# ---------- scoring -------------------------------------------------------------------

def score_job(job: Job, enr: JobEnrichment, cfg: dict) -> tuple[int, list[str]]:
    """Return (score 0..100, reasons[])."""
    s = 50
    reasons: list[str] = []
    scoring = cfg.get("scoring", {})

    # Country / region preferences
    pref_countries = [c.upper() for c in scoring.get("preferred_countries", [])]
    avoid_countries = [c.upper() for c in scoring.get("avoid_countries", [])]
    if any(c in pref_countries for c in job.countries):
        s += scoring.get("weight_preferred_country", 15); reasons.append("preferred country")
    if any(c in avoid_countries for c in job.countries):
        s -= scoring.get("weight_avoid_country", 30); reasons.append("avoided country")

    # Salary: prefer affordability ratio (local-cost-adjusted) over raw PPP.
    # Fall back to PPP threshold if affordability isn't computable.
    min_afford = scoring.get("min_affordability_ratio", 1.5)
    if enr.affordability_low is not None:
        if enr.affordability_low >= min_afford:
            s += scoring.get("weight_good_salary", 15)
            reasons.append(f"affordability {enr.affordability_low:.2f}× ≥ {min_afford}")
        elif enr.affordability_high is not None and enr.affordability_high < 1.0:
            s -= scoring.get("weight_low_salary", 15)
            reasons.append(f"affordability < 1× local cost")
    else:
        # No affordability data — fall back to absolute PPP-USD floor
        min_ppp = scoring.get("min_salary_ppp_usd", 0)
        if enr.salary_ppp_usd_low and enr.salary_ppp_usd_low >= min_ppp:
            s += scoring.get("weight_good_salary", 15)
            reasons.append(f"PPP salary ≥ ${min_ppp:,}")
        elif enr.salary_ppp_usd_high and enr.salary_ppp_usd_high < min_ppp:
            s -= scoring.get("weight_low_salary", 15)
            reasons.append(f"PPP salary < ${min_ppp:,}")

    # Experiment / keyword match (word-boundary; multi-word phrases match literally)
    desc_low = (job.description + " " + job.title).lower()
    def _has(kw: str) -> bool:
        kw = kw.lower()
        if " " in kw or "-" in kw:
            return kw in desc_low
        return re.search(rf"\b{re.escape(kw)}\b", desc_low) is not None

    keywords = scoring.get("keywords_must_have", [])
    hit_kw = [k for k in keywords if _has(k)]
    if hit_kw:
        s += scoring.get("weight_keyword_match", 10) * min(len(hit_kw), 3)
        reasons.append(f"keywords: {', '.join(hit_kw[:5])}")
    avoid_kw = scoring.get("keywords_avoid", [])
    hit_avoid = [k for k in avoid_kw if _has(k)]
    if hit_avoid:
        s -= scoring.get("weight_keyword_avoid", 20)
        reasons.append(f"avoid keywords: {', '.join(hit_avoid)}")

    # Experiment list
    pref_exp = [e.lower() for e in scoring.get("preferred_experiments", [])]
    if any(e.lower() in pref_exp for e in job.experiments):
        s += scoring.get("weight_preferred_experiment", 10)
        reasons.append("preferred experiment")

    # Deadline
    if job.deadline:
        try:
            days = (dt.date.fromisoformat(job.deadline) - dt.date.today()).days
            if days < scoring.get("min_days_to_deadline", 10):
                s -= scoring.get("weight_short_deadline", 15)
                reasons.append(f"only {days} days to deadline")
        except ValueError:
            pass

    return max(0, min(100, s)), reasons


# ---------- state DB ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    score      INTEGER,
    notified   INTEGER DEFAULT 0,
    title      TEXT,
    url        TEXT,
    PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS enrich_cache (
    source    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    payload   TEXT NOT NULL,
    created   TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
"""

def open_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def mark_seen(conn: sqlite3.Connection, job: Job, score: int) -> bool:
    """Insert if new; update last_seen otherwise. Return True iff it's new."""
    now = dt.datetime.utcnow().isoformat()
    cur = conn.execute("SELECT 1 FROM seen_jobs WHERE source=? AND source_id=?",
                       (job.source, job.source_id))
    if cur.fetchone():
        conn.execute(
            "UPDATE seen_jobs SET last_seen=?, score=? WHERE source=? AND source_id=?",
            (now, score, job.source, job.source_id),
        )
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO seen_jobs(source, source_id, first_seen, last_seen, score, title, url) "
        "VALUES (?,?,?,?,?,?,?)",
        (job.source, job.source_id, now, now, score, job.title, job.url),
    )
    conn.commit()
    return True


# ---------- main ----------------------------------------------------------------------

def load_config(path: str | Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="hep_jobs.sqlite")
    ap.add_argument("--digest-out", default="digest.md",
                    help="Path to write the Markdown digest")
    ap.add_argument("--json-out", default=None,
                    help="Path to also write a JSON digest (for the static site)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't update the DB or send mail; just print")
    ap.add_argument("--rescore-all", action="store_true",
                    help="Re-emit digest for all open jobs, not only new ones")
    ap.add_argument("--only-source", default=None,
                    help="Limit fetching to one source (e.g. 'inspire', 'ajo')")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING - 10 * args.verbose,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    conn = open_db(args.db)

    # Build sources (deferred import: sources imports from hep_jobs)
    from sources import build_sources
    sources = build_sources(cfg)
    if args.only_source:
        sources = [s for s in sources if s.name == args.only_source
                   or getattr(s, "source_key", None) == args.only_source]

    new_jobs: list[tuple[Job, JobEnrichment, int, list[str]]] = []
    n_total = 0
    n_per_source: dict[str, int] = {}
    for source in sources:
        log.info("=== source: %s ===", source.name)
        n_this = 0
        try:
            for job in source.fetch():
                n_total += 1
                n_this += 1
                enr = enrich_job(job, cfg, conn)
                score, reasons = score_job(job, enr, cfg)
                is_new = True
                if not args.dry_run:
                    is_new = mark_seen(conn, job, score)
                if is_new or args.rescore_all:
                    new_jobs.append((job, enr, score, reasons))
        except Exception as e:  # noqa: BLE001
            log.error("source %s failed: %s", source.name, e)
        n_per_source[source.name] = n_this
        log.info("source %s: %d jobs", source.name, n_this)

    log.info("fetched %d jobs total, %d new/rescored", n_total, len(new_jobs))

    min_report = cfg.get("scoring", {}).get("min_report_score", 40)
    digest = [t for t in new_jobs if t[2] >= min_report]
    digest.sort(key=lambda t: -t[2])

    write_markdown_digest(args.digest_out, digest, n_total=n_total, n_new=len(new_jobs),
                          per_source=n_per_source)
    print(f"wrote {args.digest_out} with {len(digest)} entries (of {len(new_jobs)} new)")

    if args.json_out:
        scoring_cfg = cfg.get("scoring", {})
        config_summary = {
            "preferred_countries": scoring_cfg.get("preferred_countries", []),
            "avoid_countries": scoring_cfg.get("avoid_countries", []),
            "keywords_must_have": scoring_cfg.get("keywords_must_have", []),
            "keywords_avoid": scoring_cfg.get("keywords_avoid", []),
            "preferred_experiments": scoring_cfg.get("preferred_experiments", []),
            "min_affordability_ratio": scoring_cfg.get("min_affordability_ratio"),
        }
        write_json_digest(args.json_out, digest, n_total=n_total, n_new=len(new_jobs),
                          per_source=n_per_source, config_summary=config_summary)
        print(f"wrote {args.json_out} with {len(digest)} entries")

    if cfg.get("email", {}).get("enabled") and digest and not args.dry_run:
        send_email_digest(cfg["email"], args.digest_out)
        print("email sent")

    return 0


if __name__ == "__main__":
    sys.exit(main())
