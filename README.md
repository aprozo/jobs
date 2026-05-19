# HEP jobs monitor

A small agent that polls multiple sources for open junior / postdoc positions
in HEP, scores each posting against your criteria with cost-of-living-adjusted
salary normalization, and emits a Markdown digest (optionally emailed).

## What it does

1. **Fetch from multiple sources** —
   - **INSPIRE-HEP** REST API (`/api/jobs`) — same filters as the web UI.
   - **AcademicJobsOnline** — HTML-scraped (no public API), sections `HEP`
     (experimental) and `HET` (theoretical). Many AJO postings are NOT
     cross-listed on INSPIRE.
   - **Generic RSS** — any career page that publishes a feed. Pre-configured
     for ATLAS jobs; trivially extensible.
2. **Dedup across sources** — SQLite store keyed by `(source, source_id)` so
   each posting is reported once per source.
3. **Enrich** —
   - Infers country from institution names / regions.
   - Looks up a typical gross salary range for that country (`salary_data.py`,
     sources noted inline).
   - Converts to PPP-adjusted USD.
   - **Computes affordability ratio**: `salary / typical annual living cost`
     for that country, all in local currency. >1.5 = comfortable,
     1.0–1.5 = tight, <1.0 = below local cost-of-living.
   - *Optional:* Claude or local Ollama extracts explicit salary mention,
     duration, location, experiments, topics from the description.
4. **Score** — Weighted 0–100. Affordability is the primary salary signal;
   PPP-USD floor is a fallback for countries without a living-cost entry.
5. **Notify** — `digest.md` grouped by urgency, optional SMTP email.

## Why affordability instead of just PPP?

PPP-USD compares purchasing power across countries but doesn't tell you "is
this enough to live well here." Affordability ratio does:

| Posting | Nominal | PPP-USD | Affordability |
|---------|---------|---------|---------------|
| BNL US postdoc | $65-90k | $65-90k | 1.55-2.14× (comfortable) |
| Heidelberg ATLAS | €56-70k | $73-91k | 2.33-2.92× (very comfortable) |
| CERN Fellow | CHF 84-89k tax-free | ~$80-85k | 1.56-1.65× (before tax adv.) |

Heidelberg "wins" on affordability even at lower nominal/PPP because local
prices are lower. This is the metric you actually feel.

## Honest limitations

- **Per-posting salary is usually absent.** The tool reports a *country-typical
  range*, not the actual offer.
- **AJO scraping is HTML-based** and could break if AJO changes their layout.
  Defensive code, but plan to revisit annually.
- **Country inference** is a hand-curated map of common HEP institutions.
  Misses are reported with `n/a` salary. Add to `_COUNTRY_HINTS` in
  `hep_jobs.py` for fixes, or enable LLM enrichment which infers location
  from descriptions.
- **Tables drift annually.** Sources are noted next to each entry in
  `salary_data.py`.
- **CERN tax exemption isn't modeled** — CERN's affordability is actually
  better than reported because the stipend is tax-free.
- **No cross-source dedup yet.** The same posting often appears on INSPIRE +
  AJO + experiment RSS; each shows separately for now.
- **"Worth applying" is subjective** — every weight is configurable.

## Install

```bash
git clone <wherever-you-put-this>
cd hep-jobs-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Optional, for LLM enrichment:
pip install anthropic

cp config.example.yaml config.yaml
$EDITOR config.yaml
```

## Run

```bash
# first run: populates the DB; everything is "new"
python hep_jobs.py --config config.yaml -v

# subsequent runs: only new postings
python hep_jobs.py --config config.yaml

# rebuild digest for ALL currently-open jobs
python hep_jobs.py --config config.yaml --rescore-all

# limit to one source while debugging
python hep_jobs.py --config config.yaml --only-source ajo

# preview without touching DB or sending mail
python hep_jobs.py --config config.yaml --dry-run --rescore-all
```

Output: `digest.md`, grouped:

- 🔥 Urgent (≥ 80)
- ✅ Worth applying (65–79)
- 🤔 Consider (50–64)
- 👀 Maybe (< 50)

Each entry shows source (`inspire`, `ajo`, `atlas`, …), location, deadline,
typical salary in local currency, PPP-USD equivalent, and affordability ratio.

## Adding a new source

### 1. RSS — zero code

```yaml
sources:
  - type: rss
    name: My experiment jobs
    url: https://some-experiment.org/jobs/rss.xml
    key: myexp
    default_field: hep-ex
```

### 2. Custom HTML scraper

Subclass `JobSource` in `sources.py`:
```python
class MyLabSource(JobSource):
    name = "mylab"
    def fetch(self):
        r = self._get("https://mylab.org/careers/postdocs")
        # parse with BeautifulSoup, yield Job(source="mylab", ...)
```
Add a branch in `build_sources()` and reference `type: mylab` in config.

### 3. JSON API

Same pattern as `InspireSource`. Many career portals (Workday, SuccessFactors)
have JSON endpoints findable via DevTools.

## Deploying

### cron

```cron
0 9 * * 1-5 cd /path/to/hep-jobs-monitor && /path/to/.venv/bin/python hep_jobs.py >> run.log 2>&1
```

### systemd timer

`~/.config/systemd/user/hep-jobs.service`:
```ini
[Unit]
Description=HEP jobs monitor
[Service]
Type=oneshot
WorkingDirectory=%h/hep-jobs-monitor
ExecStart=%h/hep-jobs-monitor/.venv/bin/python hep_jobs.py
```

`~/.config/systemd/user/hep-jobs.timer`:
```ini
[Unit]
Description=Run HEP jobs monitor daily
[Timer]
OnCalendar=Mon..Fri 09:00
Persistent=true
[Install]
WantedBy=timers.target
```
```bash
systemctl --user daemon-reload
systemctl --user enable --now hep-jobs.timer
```

### GitHub Actions

`.github/workflows/hep-jobs.yml`:
```yaml
name: hep-jobs
on:
  schedule: [{cron: "0 7 * * 1-5"}]
  workflow_dispatch: {}
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt
      - uses: actions/cache@v4
        with:
          path: hep_jobs.sqlite
          key: hep-jobs-db-${{ github.run_id }}
          restore-keys: hep-jobs-db-
      - env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
        run: python hep_jobs.py --config config.yaml
      - uses: actions/upload-artifact@v4
        with: {name: digest, path: digest.md}
```

GH Actions cache isn't durable across runs. For serious state, commit the
SQLite to a private repo or use a small VPS.

## Tuning

All weights are in `config.yaml`. Common changes:

- Add experiments under `preferred_experiments`
- Add jargon to `keywords_must_have` (multi-word phrases reduce false positives)
- Set `min_affordability_ratio` to your floor (1.5 = comfortable)
- Toggle `fetch_descriptions: true` on AJO for per-posting detail (slower —
  ~1 HTTP call per posting)

## Project layout

```
hep_jobs.py        # main CLI + scoring + DB + Job dataclass
sources.py         # InspireSource, AJOSource, RSSSource + build_sources()
enrichment.py      # country/salary/COL/affordability + optional LLM
salary_data.py     # POSTDOC_SCALES, PPP, COL_INDEX_*, ANNUAL_LIVING_COST_LOCAL
notify.py          # Markdown digest + SMTP
config.example.yaml
requirements.txt
```

## Things worth adding next

- **Cross-source duplicate detection** (fuzzy title + institution matching).
- **Embedding-based research-fit score** using your CV + recent abstracts.
- **PI cross-reference** — fetch the hiring PI's recent arXiv papers; overlap
  with yours is a much stronger signal than keyword matching.
- **Auto-draft cover letter** for top-scored postings (LLM + your CV).
- **Live cost-of-living** from a live API (OECD PPP is free; Numbeo paid).
- **Slack/Mattermost webhook** notifier.
