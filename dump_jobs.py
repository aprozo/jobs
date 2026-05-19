#!/usr/bin/env python3
"""Dump all scraped jobs to manual_dump.json for one-shot manual enrichment.

Reads config.yaml, fetches from every source, writes a JSON array of
{key, title, institutions, description} ready to be hand-extracted.
"""
import json
import sys
from hep_jobs import load_config
from sources import build_sources


def main():
    cfg = load_config("config.yaml")
    sources = build_sources(cfg)
    out = []
    for src in sources:
        try:
            for job in src.fetch():
                out.append({
                    "key": job.key,
                    "title": job.title,
                    "institutions": job.institutions[:5],
                    "description": (job.description or "")[:4000],
                })
        except Exception as e:
            print(f"source {src.name} failed: {e}", file=sys.stderr)
    with open("manual_dump.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"dumped {len(out)} jobs to manual_dump.json")


if __name__ == "__main__":
    main()
