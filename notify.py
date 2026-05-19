"""
notify.py — Build a Markdown digest of scored jobs and (optionally) email it.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def _fmt_money(low, high, cur) -> str:
    if not (low and high and cur):
        return "n/a"
    return f"{low:,.0f}–{high:,.0f} {cur}"


def _fmt_ppp(low, high) -> str:
    if not (low and high):
        return ""
    return f"(~\\${low:,.0f}–{high:,.0f} PPP-USD)"


def write_markdown_digest(path, entries, *, n_total: int, n_new: int,
                           per_source: dict[str, int] | None = None) -> None:
    """`entries` is a list of (Job, JobEnrichment, score, reasons[, components])."""
    entries = [(t[0], t[1], t[2], t[3]) for t in entries]
    today = dt.date.today().isoformat()
    out: list[str] = []
    out.append(f"# HEP jobs digest — {today}\n")
    summary = (f"Scanned **{n_total}** open postings, **{n_new}** new since last run, "
               f"**{len(entries)}** above report threshold.")
    if per_source:
        parts = [f"{k}: {v}" for k, v in per_source.items() if v]
        if parts:
            summary += "  \n_sources:_ " + ", ".join(parts)
    out.append(summary + "\n")
    if not entries:
        out.append("\n_No matches today._\n")
        Path(path).write_text("\n".join(out))
        return

    bucket = {"urgent": [], "apply": [], "consider": [], "skim": []}
    for e in entries:
        score = e[2]
        if score >= 80:
            bucket["urgent"].append(e)
        elif score >= 65:
            bucket["apply"].append(e)
        elif score >= 50:
            bucket["consider"].append(e)
        else:
            bucket["skim"].append(e)

    headings = [
        ("urgent",   "## 🔥 Urgent (score ≥ 80)"),
        ("apply",    "## ✅ Worth applying (score 65–79)"),
        ("consider", "## 🤔 Consider (score 50–64)"),
        ("skim",     "## 👀 Maybe (score < 50)"),
    ]
    for key, heading in headings:
        if not bucket[key]:
            continue
        out.append(f"\n{heading}\n")
        for job, enr, score, reasons in bucket[key]:
            out.append(_render_job(job, enr, score, reasons))

    Path(path).write_text("\n".join(out))


def _bucket_for(score: int) -> str:
    if score >= 80:
        return "urgent"
    if score >= 65:
        return "apply"
    if score >= 50:
        return "consider"
    return "skim"


def _affordability_label(af_low: float | None) -> str | None:
    if af_low is None:
        return None
    if af_low >= 1.5:
        return "comfortable"
    if af_low >= 1.2:
        return "OK"
    if af_low >= 1.0:
        return "tight"
    return "below local cost-of-living"


def _days_to_deadline(deadline: str | None) -> int | None:
    if not deadline:
        return None
    try:
        return (dt.date.fromisoformat(deadline) - dt.date.today()).days
    except ValueError:
        return None


def write_json_digest(path, entries, *, n_total: int, n_new: int,
                      per_source: dict[str, int] | None = None,
                      config_summary: dict | None = None,
                      default_weights: dict | None = None,
                      site_meta: dict | None = None) -> None:
    """Write a machine-readable digest for the static site.

    `entries` is a list of (Job, JobEnrichment, score, reasons, components).
    All entries are emitted unfiltered — the page applies user-facing filters
    client-side.
    """
    jobs_out: list[dict] = []
    for item in entries:
        if len(item) == 5:
            job, enr, score, reasons, components = item
        else:
            job, enr, score, reasons = item
            components = {}
        desc = job.description or ""
        short = desc[:400].rstrip() + ("…" if len(desc) > 400 else "")
        match_text = ((job.title or "") + " " + desc)[:4000].lower()
        jobs_out.append({
            "title": job.title,
            "url": job.url,
            "source": job.source,
            "also_sources": list((job.raw or {}).get("also_sources", [])),
            "alt_urls": list((job.raw or {}).get("alt_urls", [])),
            "score": int(score),
            "bucket": _bucket_for(int(score)),
            "reasons": list(reasons or []),
            "institutions": list(job.institutions or []),
            "country": enr.country,
            "countries": list(job.countries or []),
            "match_text": match_text,
            "city": enr.city,
            "col_index": enr.col_index,
            "deadline": job.deadline,
            "days_to_deadline": _days_to_deadline(job.deadline),
            "posted": job.posted,
            "ranks": list(job.ranks or []),
            "fields_of_interest": list(job.fields_of_interest or []),
            "experiments": list(job.experiments or []),
            "salary_low_local": enr.salary_low_local,
            "salary_high_local": enr.salary_high_local,
            "salary_currency": enr.salary_currency,
            "salary_source": enr.salary_source,
            "salary_ppp_usd_low": enr.salary_ppp_usd_low,
            "salary_ppp_usd_high": enr.salary_ppp_usd_high,
            "salary_usd_low": getattr(enr, "salary_usd_low", None),
            "salary_usd_high": getattr(enr, "salary_usd_high", None),
            "salary_net_local_low": enr.salary_net_local_low,
            "salary_net_local_high": enr.salary_net_local_high,
            "salary_net_ppp_usd_low": enr.salary_net_ppp_usd_low,
            "salary_net_ppp_usd_high": enr.salary_net_ppp_usd_high,
            "salary_net_usd_low": getattr(enr, "salary_net_usd_low", None),
            "salary_net_usd_high": getattr(enr, "salary_net_usd_high", None),
            "affordability_low": enr.affordability_low,
            "affordability_high": enr.affordability_high,
            "affordability_label": _affordability_label(enr.affordability_low),
            "salary_mentioned_in_post": enr.salary_mentioned_in_post,
            "salary_estimate": enr.salary_estimate,
            "llm_summary": enr.llm_summary,
            "description_short": short,
            "score_components": components,
        })

    payload = {
        "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_total": n_total,
        "n_new": n_new,
        "per_source": per_source or {},
        "config_summary": config_summary or {},
        "default_weights": default_weights or {},
        "site_meta": site_meta or {},
        "jobs": jobs_out,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _render_job(job, enr, score: int, reasons: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"\n### [{job.title}]({job.url}) — score {score}  `{job.source}`")
    if job.institutions:
        lines.append(f"- **Institutions:** {', '.join(job.institutions[:3])}")
    where = []
    if enr.city:
        where.append(enr.city)
    if enr.country:
        where.append(enr.country)
    if where:
        lines.append(f"- **Location:** {', '.join(where)}"
                     + (f"  (cost index {enr.col_index} vs NYC=100)" if enr.col_index else ""))
    if job.deadline:
        lines.append(f"- **Deadline:** {job.deadline}")
    if job.ranks:
        lines.append(f"- **Rank:** {', '.join(job.ranks)}")
    if job.experiments:
        lines.append(f"- **Experiments:** {', '.join(job.experiments[:4])}")

    sal_line = f"- **Typical salary range:** {_fmt_money(enr.salary_low_local, enr.salary_high_local, enr.salary_currency)} {_fmt_ppp(enr.salary_ppp_usd_low, enr.salary_ppp_usd_high)}"
    if enr.salary_source:
        sal_line += f"  \n  _scale: {enr.salary_source}_"
    lines.append(sal_line)

    if enr.affordability_low and enr.affordability_high:
        # Affordability >1.5 = comfortable, ~1.0 = breakeven, <1.0 = tight
        af_low, af_high = enr.affordability_low, enr.affordability_high
        if af_low >= 1.5:
            label = "comfortable"
        elif af_low >= 1.2:
            label = "OK"
        elif af_low >= 1.0:
            label = "tight"
        else:
            label = "below local cost-of-living"
        lines.append(f"- **Local affordability:** {af_low:.2f}×–{af_high:.2f}× "
                     f"typical annual cost ({label})")

    if enr.salary_mentioned_in_post:
        lines.append(f"- **Mentioned in posting:** _{enr.salary_mentioned_in_post[:300]}_")
    if enr.llm_summary:
        lines.append(f"- **Summary:** {enr.llm_summary}")
    if reasons:
        lines.append(f"- **Why this score:** {'; '.join(reasons)}")

    # short blurb of description
    blurb = job.description[:400].rstrip()
    if len(job.description) > 400:
        blurb += "…"
    lines.append(f"\n> {blurb}\n")
    return "\n".join(lines)


def send_email_digest(email_cfg: dict, digest_path: str) -> None:
    body = Path(digest_path).read_text()
    msg = EmailMessage()
    msg["From"] = email_cfg["from"]
    msg["To"] = ", ".join(email_cfg["to"]) if isinstance(email_cfg["to"], list) else email_cfg["to"]
    msg["Subject"] = email_cfg.get("subject", f"HEP jobs digest — {dt.date.today().isoformat()}")
    msg.set_content(body)
    # attach the .md as well so formatting survives
    msg.add_attachment(body.encode(), maintype="text", subtype="markdown",
                       filename=Path(digest_path).name)

    host = email_cfg["smtp_host"]
    port = int(email_cfg.get("smtp_port", 465))
    user = email_cfg.get("smtp_user") or os.getenv("SMTP_USER")
    pw   = email_cfg.get("smtp_password") or os.getenv("SMTP_PASSWORD")

    if port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx) as s:
            if user:
                s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, pw)
            s.send_message(msg)
