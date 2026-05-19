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
# CERN Careers (SmartRecruiters JSON API)
# ============================================================================

CERN_LIST = "https://api.smartrecruiters.com/v1/companies/CERN/postings"
CERN_DETAIL = "https://api.smartrecruiters.com/v1/companies/CERN/postings/{id}"
CERN_POSTING_URL = "https://jobs.smartrecruiters.com/CERN/{id}"

_CERN_DEADLINE_RE = re.compile(
    r"closing date[^A-Za-z0-9]*([0-3]?\d)[./-]([01]?\d)[./-](20\d{2})",
    re.I,
)
_CERN_EXPERIMENT_RE = re.compile(
    r"\b(ATLAS|CMS|LHCb|ALICE|FCC|ISOLDE|n_TOF|AWAKE|TOTEM|MoEDAL|NA\d+)\b",
    re.I,
)
# Drop CERN postings that are clearly outside HEP (support staff, services).
_CERN_NONHEP_RE = re.compile(
    r"\b(firefighter|fire\s*fighter|cook|chef|kitchen|driver|cleaner|"
    r"garden(er|ing)?|security\s*guard|receptionist|nurse|paramedic|"
    r"human\s*resources|hr\s*assistant|accountant|finance\s*officer|"
    r"legal\s*counsel|procurement|press|communications?\s*officer|"
    r"medical\s*doctor|psychologist|teacher|librarian|translator)\b",
    re.I,
)
# Drop generic tech / admin / studentship roles (not research positions).
# Override below keeps anything tagged as physicist/scientist/postdoc/fellow.
_CERN_GENERIC_ROLE_RE = re.compile(
    r"\b(engineer|technician|devops|developer|"
    r"technical\s+studentship|short\s+term\s+internship|"
    r"spontaneous\s+applications|"
    r"administrative\s+(assistant|student)|protocol\s+officer|"
    r"executive\s+(personal\s+)?assistant|financial\s+officer|"
    r"project\s+manager|e-learning)\b",
    re.I,
)
# Override: keep CERN postings that explicitly read as research roles.
_CERN_RESEARCH_OVERRIDE_RE = re.compile(
    r"\b(physicist|scientist|postdoc(toral)?|fellow(ship)?|"
    r"doctoral\s+student|theoretical\s+physics|experimental\s+physics|"
    r"applied\s+physicist|computational\s+physicist|"
    r"research\s+associate)\b",
    re.I,
)


class CERNSource(JobSource):
    """CERN careers via the public SmartRecruiters API.

    Covers fellowships, staff, applied/detector/accelerator/computing roles
    hosted at CERN — which is where most LHC-experiment staff jobs land.
    """
    name = "cern"

    def __init__(self, fetch_descriptions: bool = True, max_results: int = 200,
                 only_research: bool = False):
        self.fetch_descriptions = fetch_descriptions
        self.max_results = max_results
        self.only_research = only_research

    def fetch(self) -> Iterable[Job]:
        offset = 0
        page_size = 100
        seen = 0
        while seen < self.max_results:
            params = {"limit": min(page_size, self.max_results - seen),
                      "offset": offset, "active": "true"}
            url = f"{CERN_LIST}?{urlencode(params)}"
            log.info("[cern] %s", url)
            try:
                r = self._get(url, headers={"Accept": "application/json"})
            except Exception as e:  # noqa: BLE001
                log.warning("[cern] list fetch failed: %s", e)
                return
            data = r.json()
            postings = data.get("content") or []
            if not postings:
                break
            for p in postings:
                if self.only_research and (p.get("industry", {}).get("id") != "research"):
                    continue
                function_label = (p.get("function") or {}).get("label") or ""
                title = p.get("name") or ""
                blob = function_label + " " + title
                if _CERN_NONHEP_RE.search(blob):
                    continue
                # Drop generic tech / admin / studentship unless overridden by
                # an explicit research-role keyword in the title.
                if (_CERN_GENERIC_ROLE_RE.search(blob)
                        and not _CERN_RESEARCH_OVERRIDE_RE.search(title)):
                    continue
                yield self._build_job(p)
                seen += 1
                if seen >= self.max_results:
                    break
            total = data.get("totalFound", 0)
            offset += len(postings)
            if offset >= total:
                break
            time.sleep(0.25)

    def _build_job(self, p: dict) -> Job:
        pid = str(p.get("id") or p.get("uuid"))
        name = p.get("name") or "(untitled)"
        # Strip trailing CERN ref code like "(TE-MPE-MP-2026-141-GRAE)"
        title = re.sub(r"\s*\([A-Z0-9-]{6,}\)\s*$", "", name).strip()
        loc = p.get("location", {}) or {}
        country = (loc.get("country") or "").upper() or None
        city = loc.get("city")
        countries = [country] if country else []

        description = ""
        deadline = None
        if self.fetch_descriptions:
            try:
                d = self._get(CERN_DETAIL.format(id=pid),
                              headers={"Accept": "application/json"}).json()
                ja = d.get("jobAd", {}).get("sections", {}) or {}
                parts = []
                for k in ("companyDescription", "jobDescription", "qualifications",
                          "additionalInformation"):
                    txt = (ja.get(k) or {}).get("text") or ""
                    if txt:
                        parts.append(_strip_html(txt))
                description = " ".join(parts)[:6000]
                m = _CERN_DEADLINE_RE.search(description)
                if m:
                    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
                    deadline = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
                time.sleep(0.15)
            except Exception as e:  # noqa: BLE001
                log.debug("[cern] detail failed for %s: %s", pid, e)

        # Try to guess experiment from title + description
        experiments = []
        for hit in _CERN_EXPERIMENT_RE.findall(title + " " + description):
            up = hit.upper()
            if up not in experiments:
                experiments.append(up)

        dept = (p.get("department") or {}).get("label") or ""
        function = (p.get("function") or {}).get("label") or ""
        is_theory = any("theor" in s.lower() for s in (dept, function, title))
        field = "hep-ph" if is_theory else "hep-ex"

        exp_level = ((p.get("experienceLevel") or {}).get("id") or "").lower()
        prog = ""
        for cf in p.get("customField", []) or []:
            if (cf.get("fieldId") or "").lower() == "programme" or cf.get("fieldLabel") == "Programme":
                prog = (cf.get("valueLabel") or "").lower()
                break
        ranks = self._classify_rank(exp_level, prog, title)

        posted = p.get("releasedDate")
        url = CERN_POSTING_URL.format(id=pid)

        return Job(
            source="cern",
            source_id=pid,
            title=title,
            institutions=["CERN"],
            regions=["Europe"],
            countries=countries,
            ranks=ranks,
            fields_of_interest=[field],
            experiments=experiments,
            description=description,
            deadline=deadline,
            posted=posted,
            url=url,
            raw={"city": city, "department": dept, "function": function,
                 "programme": prog, "ref": p.get("refNumber")},
        )

    @staticmethod
    def _classify_rank(exp_level: str, programme: str, title: str) -> list[str]:
        t = title.lower()
        if "fellow" in t or "fellow" in programme:
            return ["JUNIOR"]
        if "postdoc" in t or "research associate" in t:
            return ["POSTDOC"]
        if "phd" in t or "doctoral" in t or "graduate" in programme:
            return ["PHD"]
        if "early career" in programme:
            return ["JUNIOR"]
        if exp_level in {"entry_level", "associate"}:
            return ["JUNIOR"]
        if exp_level in {"experienced", "mid_senior", "director", "executive"}:
            return ["SENIOR"]
        return []


# ============================================================================
# EURAXESS (HTML scrape of the EU jobs portal)
# ============================================================================

EURAXESS_BASE = "https://euraxess.ec.europa.eu"
EURAXESS_SEARCH = "https://euraxess.ec.europa.eu/jobs/search"

_EURAXESS_JOB_HREF_RE = re.compile(r"/jobs/(\d+)")


class EuraxessSource(JobSource):
    """Scrape EURAXESS jobs portal filtered by Physics research field.

    EURAXESS exposes only an HTML search page that requires a browser-like
    User-Agent. We page through the search results and yield one Job per
    posting; detail fetch is optional (slower).
    """
    name = "euraxess"

    def __init__(self, domains: list[str] | None = None,
                 keywords: str = "",
                 max_pages: int = 5,
                 fetch_descriptions: bool = False):
        self.domains = list(domains or ["Physics"])
        self.keywords = keywords
        self.max_pages = max_pages
        self.fetch_descriptions = fetch_descriptions

    def _ua(self) -> dict:
        return {
            "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml",
        }

    def fetch(self) -> Iterable[Job]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            log.error("EuraxessSource requires beautifulsoup4")
            return
        seen: set[str] = set()
        for page in range(self.max_pages):
            params = [("page", str(page))]
            for d in self.domains:
                params.append(("domains[]", d))
            if self.keywords:
                params.append(("keywords", self.keywords))
            url = f"{EURAXESS_SEARCH}?{urlencode(params, doseq=True)}"
            log.info("[euraxess] %s", url)
            try:
                r = self._get(url, headers=self._ua())
            except Exception as e:  # noqa: BLE001
                log.warning("[euraxess] page %s failed: %s", page, e)
                break
            soup = BeautifulSoup(r.text, "html.parser")
            page_jobs = list(self._parse_search(soup, seen))
            if not page_jobs:
                break
            for j in page_jobs:
                yield j
            time.sleep(0.5)

    def _parse_search(self, soup, seen: set) -> Iterable[Job]:
        # Each result row is typically an <article> or <div class="search-result">
        # with a heading link to /jobs/<id>.
        for a in soup.find_all("a", href=_EURAXESS_JOB_HREF_RE):
            m = _EURAXESS_JOB_HREF_RE.search(a.get("href", ""))
            if not m:
                continue
            jid = m.group(1)
            if jid in seen:
                continue
            seen.add(jid)
            title = " ".join(a.stripped_strings).strip()
            if not title:
                continue

            # The result card holds employer name, country, deadline in siblings.
            card = a.find_parent(
                lambda el: el and el.name in ("article", "div", "li")
                and (el.get("class") or [])
            ) or a.parent

            employer = ""
            country = ""
            deadline = ""
            for el in (card.find_all(class_=re.compile(r"organisation|employer|institution", re.I))
                       if card else []):
                t = " ".join(el.stripped_strings)
                if t:
                    employer = t
                    break
            for el in (card.find_all(class_=re.compile(r"country|location", re.I))
                       if card else []):
                t = " ".join(el.stripped_strings)
                if t:
                    country = t
                    break
            for el in (card.find_all(class_=re.compile(r"deadline|date", re.I))
                       if card else []):
                t = " ".join(el.stripped_strings)
                if re.search(r"\d{4}", t):
                    deadline = t
                    break

            # Normalise deadline if it's a date-like string
            dl_iso = None
            md = re.search(r"(\d{1,2})[/ -]([A-Za-z]+|\d{1,2})[/ -](20\d{2})", deadline)
            if md:
                try:
                    import datetime as _dt
                    dd, mo, yy = md.groups()
                    if mo.isalpha():
                        dt = _dt.datetime.strptime(f"{dd} {mo} {yy}", "%d %B %Y")
                    else:
                        dt = _dt.datetime(int(yy), int(mo), int(dd))
                    dl_iso = dt.date().isoformat()
                except ValueError:
                    dl_iso = None

            description = ""
            if self.fetch_descriptions:
                try:
                    full = self._get(urljoin(EURAXESS_BASE, a.get("href")),
                                     headers=self._ua())
                    description = _strip_html(full.text)[:6000]
                except Exception as e:  # noqa: BLE001
                    log.debug("[euraxess] detail %s: %s", jid, e)

            iso = self._country_to_iso(country) or self._country_to_iso(employer)
            countries = [iso] if iso else _infer_countries(
                [], [employer] if employer else [], title + " " + description
            )

            yield Job(
                source="euraxess",
                source_id=jid,
                title=title,
                institutions=[employer] if employer else [],
                regions=["Europe"],
                countries=countries,
                ranks=AJOSource._guess_rank(title),
                fields_of_interest=["hep-ex"],
                experiments=[],
                description=description,
                deadline=dl_iso,
                posted=None,
                url=urljoin(EURAXESS_BASE, a.get("href")),
                raw={"country_text": country, "deadline_text": deadline},
            )

    @staticmethod
    def _country_to_iso(text: str) -> str | None:
        if not text:
            return None
        t = text.strip().upper()
        # Quick country-name → ISO map for the EU-heavy EURAXESS dataset
        m = {
            "GERMANY": "DE", "FRANCE": "FR", "SWITZERLAND": "CH", "UNITED KINGDOM": "GB",
            "UK": "GB", "ITALY": "IT", "SPAIN": "ES", "NETHERLANDS": "NL",
            "BELGIUM": "BE", "POLAND": "PL", "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ",
            "AUSTRIA": "AT", "SWEDEN": "SE", "DENMARK": "DK", "NORWAY": "NO",
            "FINLAND": "FI", "PORTUGAL": "PT", "GREECE": "GR", "HUNGARY": "HU",
            "IRELAND": "IE", "ROMANIA": "RO", "SLOVENIA": "SI", "SLOVAKIA": "SK",
            "BULGARIA": "BG", "ESTONIA": "EE", "LATVIA": "LV", "LITHUANIA": "LT",
            "CROATIA": "HR", "LUXEMBOURG": "LU", "MALTA": "MT", "CYPRUS": "CY",
            "ICELAND": "IS", "ISRAEL": "IL", "TURKEY": "TR",
        }
        for k, v in m.items():
            if k in t:
                return v
        return None


# ============================================================================
# DESY careers (HTML scrape)
# ============================================================================

DESY_LISTING = "https://v22.desy.de/index_eng.html"
GSI_LISTING = "https://www.gsi.de/en/jobscareer/job_offers"

# Shared denylist + allowlist for HTML-scraped HEP labs that mix support
# staff with research roles (DESY, GSI). CERN runs its own function-label
# filter via the SmartRecruiters API.
_NON_HEP_ROLE_RE = re.compile(
    r"\b("
    # English support roles
    r"firefighter|cook|chef|kitchen|driver|cleaner|garden(er|ing)?|"
    r"security\s*guard|receptionist|nurse|paramedic|"
    r"human\s*resources|hr\s*assistant|accountant|finance\s*officer|"
    r"legal\s*counsel|procurement|press\s+(officer|relations)|"
    r"communications?\s*officer|medical\s*doctor|psychologist|teacher|"
    r"librarian|translator|"
    # German construction/admin/skilled-trades
    r"bauingen|tiefbau|hochbau|verwaltung|einkaufs|beschaffung|"
    r"technische[srn]?\s+(zeichner|produktdesigner)|"
    r"bim[-\s]?(autor|modellier)|"
    r"geb(a|ä)udeausr(u|ü)stung|k(u|ü)hlwasser|hausmeister|"
    r"teamassistenz|arbeitssicherheit|chemisch[-\s]technische[rs]?\s+assistent|"
    r"techniker(?:in)?\s+mechatron|"
    r"projektmanagement\s+f(u|ü)r|nachhaltigkeitsmanagement|"
    r"aushilfskraft|dual(?:es)?\s+studium|ausbildung|praktikum|"
    r"physiotherap|pharmazeut|"
    # FAIR/GSI specific admin
    r"reinraumlabor|strahlenschutz(?!.*physic)|"
    r"\bbim\b"
    r")\b",
    re.I,
)
_HEP_ROLE_HINT_RE = re.compile(
    r"\b("
    r"postdoc|post-doc|phd|ph\.?d\.?|fellow(?:ship)?|scientist|"
    r"research(?:er|\s+associate)?|"
    r"physiker|physicist|wissenschaftliche[rn]?\s+mitarbeiter|"
    r"professor|junior|senior\s+(staff|researcher|scientist)|"
    r"associate\s+professor|assistant\s+professor|"
    r"detector|accelerator|beam(?:line)?|photon|electron\s+beam|"
    r"laser\s+(physics|systems?)|theoretical|theoretische|experimental|"
    r"experimentell|simulation|cryogen|condensed\s+matter|"
    r"computing\s+(physicist|scientist)|"
    r"nuclear|particle|astro(?:nomy|physics)|FEL|synchrotron|"
    r"machine\s+learning.*(physic|beam|diagnostic|fel)|"
    r"control\s+systems?\s+(for|f(u|ü)r).*(physic|beam|accelerator)|"
    r"hochenergie|fusion|plasma|"
    r"superconduct|cavit(y|ies)|RF\s+(physics|control)"
    r")\b",
    re.I,
)


def _is_hep_role(text: str) -> bool:
    """True iff text matches a HEP-relevant role hint and no support-role denylist."""
    if _NON_HEP_ROLE_RE.search(text):
        return False
    return bool(_HEP_ROLE_HINT_RE.search(text))


class DESYSource(JobSource):
    """DESY (Hamburg + Zeuthen) job offers — single HTML listing page.

    DESY publishes one English-language careers index that links to per-job
    detail pages. We scrape the listing and, if requested, fetch each detail
    page for the full description and deadline.
    """
    name = "desy"

    def __init__(self, fetch_descriptions: bool = False):
        self.fetch_descriptions = fetch_descriptions

    def fetch(self) -> Iterable[Job]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            log.error("DESYSource requires beautifulsoup4")
            return
        try:
            r = self._get(DESY_LISTING)
        except Exception as e:  # noqa: BLE001
            log.warning("[desy] list fetch failed: %s", e)
            return
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", id="desy_joblist")
        if table is None:
            log.warning("[desy] desy_joblist table not found on listing page")
            return
        seen: set[str] = set()
        for tr in table.select("tbody tr"):
            onclick = tr.get("onclick", "")
            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if not m:
                continue
            full = m.group(1)
            if full in seen:
                continue
            seen.add(full)
            # The row's title attribute carries the job code + short description
            # joined by a colon: e.g. "FSPO005/2026 Postdoc on …: short blurb".
            row_title = tr.get("title") or ""
            row_title = row_title.replace("\r", " ").replace("\n", " ").strip()
            if ":" in row_title:
                head, _, blurb = row_title.partition(":")
            else:
                head, blurb = row_title, ""
            head = head.strip()
            blurb = blurb.strip()
            # Strip leading job code (e.g. "FSPO005/2026 ") from the title.
            m_code = re.match(r"^([A-Z]{2,6}\d{3,4}/20\d{2})\s+(.*)$", head)
            code = m_code.group(1) if m_code else ""
            title = (m_code.group(2) if m_code else head).strip()
            if not title:
                continue
            # Skip DESY postings that are clearly outside HEP.
            if not _is_hep_role(title + " " + blurb):
                continue
            jid = code or re.sub(r"[^A-Za-z0-9]+", "-", full)[-80:]
            posted = tr.get("data-attr_active_start") or None
            description = blurb
            deadline = None
            if self.fetch_descriptions:
                try:
                    d = self._get(full)
                    full_text = _strip_html(d.text)[:6000]
                    description = full_text or blurb
                    m_dl = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", full_text)
                    if m_dl:
                        dd, mm, yyyy = m_dl.groups()
                        deadline = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
                    time.sleep(0.15)
                except Exception as e:  # noqa: BLE001
                    log.debug("[desy] detail %s: %s", full, e)
            yield Job(
                source="desy",
                source_id=jid,
                title=title,
                institutions=["DESY"],
                regions=["Europe"],
                countries=["DE"],
                ranks=AJOSource._guess_rank(title),
                fields_of_interest=["hep-ex"],
                experiments=[],
                description=description,
                deadline=deadline,
                posted=posted,
                url=full,
                raw={"code": code},
            )


# ============================================================================
# GSI Helmholtzzentrum für Schwerionenforschung (Darmstadt) + FAIR
# ============================================================================

class GSISource(JobSource):
    """GSI/FAIR job listings — TYPO3 page that prints one anchor per posting.

    Many GSI postings are skilled trades / administration; we apply the
    shared HEP role filter before yielding.
    """
    name = "gsi"

    def __init__(self, fetch_descriptions: bool = False):
        self.fetch_descriptions = fetch_descriptions

    def fetch(self) -> Iterable[Job]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError:
            log.error("GSISource requires beautifulsoup4")
            return
        try:
            r = self._get(GSI_LISTING)
        except Exception as e:  # noqa: BLE001
            log.warning("[gsi] list fetch failed: %s", e)
            return
        soup = BeautifulSoup(r.text, "html.parser")
        seen: set[str] = set()
        for a in soup.select("h5.jobs_title a.jobs-listing-headline"):
            href = a.get("href") or ""
            full = urljoin(GSI_LISTING, href)
            if full in seen:
                continue
            seen.add(full)
            span = a.find("span")
            title = (span.get_text(" ", strip=True) if span else
                     a.get_text(" ", strip=True))
            tail = a.get_text(" ", strip=True).replace(title, "").strip()
            ref_match = re.search(r"Ref\.\s*No\.\s*:\s*([A-Z0-9.\-]+)", tail, re.I)
            ref = ref_match.group(1) if ref_match else ""
            if not title or len(title) < 8:
                continue
            if not _is_hep_role(title):
                continue
            description = ""
            deadline = None
            if self.fetch_descriptions:
                try:
                    d = self._get(full)
                    description = _strip_html(d.text)[:6000]
                    m_dl = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", description)
                    if m_dl:
                        dd, mm, yyyy = m_dl.groups()
                        deadline = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
                    time.sleep(0.15)
                except Exception as e:  # noqa: BLE001
                    log.debug("[gsi] detail %s: %s", full, e)
            jid = ref or re.sub(r"[^A-Za-z0-9]+", "-", full)[-80:]
            yield Job(
                source="gsi",
                source_id=jid,
                title=title,
                institutions=["GSI", "FAIR"],
                regions=["Europe"],
                countries=["DE"],
                ranks=AJOSource._guess_rank(title),
                fields_of_interest=["hep-ex"],
                experiments=[],
                description=description,
                deadline=deadline,
                posted=None,
                url=full,
                raw={"ref": ref},
            )


# ============================================================================
# Workday CXS source — usable for any DOE national lab or US university that
# hosts careers on a Workday tenant (BNL, FNAL Quanta, SLAC, NREL, …).
# ============================================================================

class WorkdaySource(JobSource):
    """Pull a Workday Customer Experience Service (CXS) jobs feed.

    Example endpoint:
      https://bnl.wd1.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}/jobs

    The HTML portal at /en-US/{site_id} won't return JSON; you must POST to
    the CXS path with body `{"limit": N, "offset": M, "searchText": "…",
    "appliedFacets": {…}}`. The list response gives `externalPath` per
    posting; the detail call returns the full description + locations.
    """

    def __init__(self, *, name: str, tenant: str, site_id: str,
                 search_text: str = "postdoc", country: str | None = None,
                 institution: str | None = None,
                 max_results: int = 100, fetch_descriptions: bool = True,
                 default_field: str = "hep-ex"):
        self.name = name
        self.source_key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        self.tenant = tenant
        self.site_id = site_id
        self.search_text = search_text
        self.country = (country or "").upper() or None
        self.institution = institution or name
        self.max_results = max_results
        self.fetch_descriptions = fetch_descriptions
        self.default_field = default_field

    def _api(self, path: str) -> str:
        return (f"https://{self.tenant}.wd1.myworkdayjobs.com"
                f"/wday/cxs/{self.tenant}/{self.site_id}{path}")

    def fetch(self) -> Iterable[Job]:
        offset = 0
        page_size = 20
        seen = 0
        while seen < self.max_results:
            body = {
                "limit": page_size,
                "offset": offset,
                "searchText": self.search_text,
                "appliedFacets": {},
            }
            url = self._api("/jobs")
            log.info("[%s] %s offset=%d", self.source_key, url, offset)
            try:
                r = requests.post(url, json=body, timeout=15,
                                  headers={"Accept": "application/json",
                                           "Content-Type": "application/json",
                                           "User-Agent": "hep-jobs/1.0"})
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] list fetch failed: %s", self.source_key, e)
                return
            data = r.json()
            postings = data.get("jobPostings") or []
            if not postings:
                break
            for p in postings:
                title = p.get("title") or "(untitled)"
                ext_path = p.get("externalPath") or ""
                portal_url = (f"https://{self.tenant}.wd1.myworkdayjobs.com"
                              f"/en-US/{self.site_id}{ext_path}")
                jid = ext_path.rsplit("/", 1)[-1] or title
                description = ""
                deadline = None
                if self.fetch_descriptions and ext_path:
                    try:
                        d = requests.get(self._api(ext_path), timeout=15,
                                         headers={"Accept": "application/json",
                                                  "User-Agent": "hep-jobs/1.0"})
                        d.raise_for_status()
                        jd = d.json().get("jobPostingInfo", {})
                        description = _strip_html(jd.get("jobDescription") or "")[:6000]
                        deadline = (jd.get("endDate") or jd.get("postedOn") or None)
                        if deadline and "T" in deadline:
                            deadline = deadline.split("T", 1)[0]
                        time.sleep(0.2)
                    except Exception as e:  # noqa: BLE001
                        log.debug("[%s] detail %s: %s", self.source_key, ext_path, e)
                yield Job(
                    source=self.source_key,
                    source_id=jid,
                    title=title,
                    institutions=[self.institution],
                    regions=[],
                    countries=[self.country] if self.country else [],
                    ranks=AJOSource._guess_rank(title),
                    fields_of_interest=[self.default_field],
                    experiments=[],
                    description=description,
                    deadline=deadline,
                    posted=p.get("postedOn"),
                    url=portal_url,
                    raw={"externalPath": ext_path},
                )
                seen += 1
                if seen >= self.max_results:
                    break
            offset += len(postings)
            total = data.get("total") or 0
            if offset >= total:
                break
            time.sleep(0.25)


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
            elif t == "cern":
                out.append(CERNSource(
                    fetch_descriptions=entry.get("fetch_descriptions", True),
                    max_results=entry.get("max_results", 200),
                    only_research=entry.get("only_research", False),
                ))
            elif t == "euraxess":
                out.append(EuraxessSource(
                    domains=entry.get("domains"),
                    keywords=entry.get("keywords", ""),
                    max_pages=entry.get("max_pages", 5),
                    fetch_descriptions=entry.get("fetch_descriptions", False),
                ))
            elif t == "desy":
                out.append(DESYSource(
                    fetch_descriptions=entry.get("fetch_descriptions", False),
                ))
            elif t == "gsi":
                out.append(GSISource(
                    fetch_descriptions=entry.get("fetch_descriptions", False),
                ))
            elif t == "workday":
                out.append(WorkdaySource(
                    name=entry.get("name", "Workday"),
                    tenant=entry["tenant"],
                    site_id=entry["site_id"],
                    search_text=entry.get("search_text", "postdoc"),
                    country=entry.get("country"),
                    institution=entry.get("institution"),
                    max_results=entry.get("max_results", 100),
                    fetch_descriptions=entry.get("fetch_descriptions", True),
                    default_field=entry.get("default_field", "hep-ex"),
                ))
            else:
                log.warning("unknown source type: %s", t)
    else:
        out.append(InspireSource(cfg.get("inspire_filters", {})))
    return out
