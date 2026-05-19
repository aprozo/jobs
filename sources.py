"""
sources.py — Job-source adapters.

Each source class produces an iterable of normalized Job objects.
Add new sources by subclassing JobSource.
"""
from __future__ import annotations

import abc
import logging
import re
import time
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urlencode, urljoin
from xml.etree import ElementTree as ET

import requests

from hep_jobs import Job, _strip_html, _infer_countries, _normalize as _inspire_normalize

UA = "hep-jobs-monitor/0.2 (+https://github.com/yourname/hep-jobs-monitor)"
log = logging.getLogger("hep-jobs.sources")


# ============================================================================
# Base class
# ============================================================================

class JobSource(abc.ABC):
    """Abstract job source. Subclasses implement fetch()."""
    name: str = "unknown"

    @abc.abstractmethod
    def fetch(self) -> Iterable[Job]:
        ...

    def _get(self, url: str, **kw) -> requests.Response:
        headers = {"User-Agent": UA, **kw.pop("headers", {})}
        for attempt in range(3):
            r = requests.get(url, headers=headers, timeout=30, **kw)
            if r.status_code == 429:
                log.warning("[%s] rate limited, sleeping 6s", self.name)
                time.sleep(6)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"giving up after 3 retries on {url}")


# ============================================================================
# INSPIRE-HEP source
# ============================================================================

INSPIRE_API = "https://inspirehep.net/api/jobs"


class InspireSource(JobSource):
    name = "inspire"

    def __init__(self, filters: dict, max_pages: int = 20, page_size: int = 50):
        self.filters = filters or {}
        self.max_pages = max_pages
        self.page_size = page_size

    def fetch(self) -> Iterable[Job]:
        page = 1
        seen = 0
        while page <= self.max_pages:
            params = [("size", str(self.page_size)), ("page", str(page))]
            for k, v in self.filters.items():
                if isinstance(v, (list, tuple)):
                    for vv in v:
                        params.append((k, vv))
                else:
                    params.append((k, str(v)))
            url = f"{INSPIRE_API}?{urlencode(params)}"
            log.debug("GET %s", url)
            r = self._get(url, headers={"Accept": "application/json"})
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                yield _inspire_normalize(hit)
                seen += 1
            total = data.get("hits", {}).get("total", 0)
            if seen >= total:
                break
            page += 1
            time.sleep(0.4)


# ============================================================================
# AcademicJobsOnline (AJO) source
# ============================================================================

AJO_BASE = "https://academicjobsonline.org"

# Section codes used on AJO listing URLs: /ajo/{code}
#   HEP  = experimental high-energy
#   HET  = theoretical high-energy
#   HEP-PHENO, HEP-LAT etc. exist too but most are subsumed by HEP/HET.
AJO_SECTIONS = ("HEP", "HET")

# AJO listings have rows like:
#   <a href="/ajo/jobs/12345">[CODE] Title (deadline 2026/05/15 11:59PM)</a>
# preceded by an employer header like "<b>University of X, Department of Y</b>"
_AJO_JOB_HREF_RE = re.compile(r"/ajo/jobs/(\d+)")
_AJO_DEADLINE_RE = re.compile(r"deadline\s+(\d{4})/(\d{2})/(\d{2})", re.I)


class AJOSource(JobSource):
    """Scrape AcademicJobsOnline section pages for HEP postings.

    AJO has no public API or RSS. We scrape the public HTML pages
    (cookies not required). Each listing page lists all open postings
    for that section, grouped by employer.
    """
    name = "ajo"

    def __init__(self, sections: list[str] | None = None,
                 fetch_descriptions: bool = False):
        self.sections = list(sections or AJO_SECTIONS)
        self.fetch_descriptions = fetch_descriptions

    def fetch(self) -> Iterable[Job]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            log.error("AJOSource requires beautifulsoup4 (pip install beautifulsoup4)")
            return
        for section in self.sections:
            url = f"{AJO_BASE}/ajo/{section}"
            log.info("[ajo] fetching %s", url)
            try:
                r = self._get(url)
            except Exception as e:  # noqa: BLE001
                log.warning("[ajo] %s: %s", url, e)
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            yield from self._parse_section(soup, section)

    def _parse_section(self, soup, section: str) -> Iterable[Job]:
        """Find all job links and the nearest preceding employer header."""
        try:
            from bs4 import NavigableString  # type: ignore  # noqa: F401
        except ImportError:
            return

        seen_ids = set()
        # Walk every <a> that links to /ajo/jobs/<id>
        for a in soup.find_all("a", href=_AJO_JOB_HREF_RE):
            m = _AJO_JOB_HREF_RE.search(a.get("href", ""))
            if not m:
                continue
            jid = m.group(1)
            if jid in seen_ids:
                continue
            seen_ids.add(jid)

            link_text = " ".join(a.stripped_strings)
            if not link_text or link_text.lower() in {"apply", "details"}:
                # the Apply link — find the title link nearby
                continue

            # The employer header is the most recent <b> / <h*> / <strong> before this <a>
            employer = self._find_employer(a)

            # Title: strip the [CODE] prefix and "(deadline ...)" suffix
            title_clean = re.sub(r"^\s*\[[^\]]+\]\s*", "", link_text)
            title_clean = re.sub(r"\s*\(deadline\s+[^)]+\)\s*$", "", title_clean, flags=re.I).strip()

            # Deadline
            deadline = None
            dm = _AJO_DEADLINE_RE.search(link_text)
            if dm:
                deadline = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"

            url = f"{AJO_BASE}/ajo/jobs/{jid}"

            # Optionally fetch the detail page for description + location
            description = ""
            countries: list[str] = []
            if self.fetch_descriptions:
                try:
                    desc_r = self._get(url)
                    description = _strip_html(desc_r.text)[:4000]
                    countries = _infer_countries([], [employer] if employer else [], description)
                except Exception as e:  # noqa: BLE001
                    log.debug("[ajo] detail fetch failed for %s: %s", jid, e)
            else:
                countries = _infer_countries([], [employer] if employer else [], title_clean)

            yield Job(
                source="ajo",
                source_id=jid,
                title=title_clean,
                institutions=[employer] if employer else [],
                regions=[],
                countries=countries,
                ranks=self._guess_rank(title_clean),
                fields_of_interest=[self._section_to_field(section)],
                experiments=[],
                description=description,
                deadline=deadline,
                posted=None,
                url=url,
                raw={"section": section, "link_text": link_text},
            )

    @staticmethod
    def _find_employer(a_tag) -> str:
        """Walk backwards through siblings to find the nearest <b>/<strong>/<h*>."""
        node = a_tag
        for _ in range(50):
            node = node.find_previous(["b", "strong", "h1", "h2", "h3", "h4"])
            if node is None:
                return ""
            txt = " ".join(node.stripped_strings)
            if txt and len(txt) > 4 and not txt.startswith("["):
                return txt
        return ""

    @staticmethod
    def _section_to_field(section: str) -> str:
        return {"HEP": "hep-ex", "HET": "hep-ph", "HEP-LAT": "hep-lat",
                "HEP-PHENO": "hep-ph"}.get(section.upper(), section.lower())

    @staticmethod
    def _guess_rank(title: str) -> list[str]:
        t = title.lower()
        if "postdoc" in t or "post-doc" in t or "post doctoral" in t or "research associate" in t:
            return ["POSTDOC"]
        if "phd" in t or "ph.d. student" in t or "doctoral student" in t:
            return ["PHD"]
        if any(k in t for k in ("assistant professor", "junior researcher", "junior")):
            return ["JUNIOR"]
        if any(k in t for k in ("associate professor", "senior", "tenured")):
            return ["SENIOR"]
        return []


# ============================================================================
# Generic RSS source — usable for ATLAS, CMS-related sites with a feed.
# ============================================================================

class RSSSource(JobSource):
    """Pull a generic RSS feed and turn each <item> into a Job.

    Good for the ATLAS jobs feed (https://atlas.cern/Discover/Collaboration/Jobs)
    and any other career page that publishes one. Many do.
    """
    def __init__(self, name: str, url: str, source_key: str | None = None,
                 default_field: str = "hep-ex"):
        self.name = name
        self.url = url
        self.source_key = source_key or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        self.default_field = default_field

    def fetch(self) -> Iterable[Job]:
        log.info("[%s] fetching RSS %s", self.source_key, self.url)
        try:
            r = self._get(self.url)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] feed fetch failed: %s", self.source_key, e)
            return
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            log.warning("[%s] RSS parse failed: %s", self.source_key, e)
            return

        # Both classic RSS 2.0 (<channel><item>) and Atom (<entry>) supported.
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for item in items:
            yield self._parse_item(item)

    def _parse_item(self, item) -> Job:
        ATOM = "{http://www.w3.org/2005/Atom}"

        def _txt(tag: str) -> str:
            el = item.find(tag)
            if el is None:
                el = item.find(f"{ATOM}{tag}")
            return (el.text or "").strip() if el is not None else ""

        title = _txt("title")
        link = _txt("link")
        if not link:
            # Atom uses <link href="..."/>
            link_el = item.find(f"{ATOM}link")
            if link_el is not None:
                link = link_el.get("href", "")
        guid = _txt("guid") or _txt("id") or link or title
        description = _strip_html(_txt("description") or _txt("summary") or _txt("content"))
        pubdate = _txt("pubDate") or _txt("published") or _txt("updated")
        try:
            posted = parsedate_to_datetime(pubdate).isoformat() if pubdate else None
        except (TypeError, ValueError):
            posted = pubdate or None

        countries = _infer_countries([], [], title + " " + description)

        return Job(
            source=self.source_key,
            source_id=guid[:200] or "unknown",
            title=title or "(untitled)",
            institutions=[],
            regions=[],
            countries=countries,
            ranks=AJOSource._guess_rank(title),
            fields_of_interest=[self.default_field],
            experiments=[],
            description=description,
            deadline=None,
            posted=posted,
            url=link,
            raw={"feed": self.url},
        )


# ============================================================================
# Factory: build sources from config
# ============================================================================

def build_sources(cfg: dict) -> list[JobSource]:
    """Build the list of JobSource instances from the config 'sources' block.

    If 'sources' is absent, fall back to the single 'inspire_filters' block for
    backward compatibility with the original config layout.
    """
    out: list[JobSource] = []
    if "sources" in cfg:
        for entry in cfg["sources"]:
            t = entry.get("type")
            if t == "inspire":
                out.append(InspireSource(entry.get("filters", {})))
            elif t == "ajo":
                out.append(AJOSource(
                    sections=entry.get("sections"),
                    fetch_descriptions=entry.get("fetch_descriptions", False),
                ))
            elif t == "rss":
                out.append(RSSSource(
                    name=entry.get("name", "rss"),
                    url=entry["url"],
                    source_key=entry.get("key"),
                    default_field=entry.get("default_field", "hep-ex"),
                ))
            else:
                log.warning("unknown source type: %s", t)
    else:
        out.append(InspireSource(cfg.get("inspire_filters", {})))
    return out
