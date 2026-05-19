"""
salary_data.py — Static knowledge base of typical HEP postdoc gross compensation
and cost-of-living indices, by country / institution type.

These numbers are MID-2025 / EARLY-2026 best-effort estimates from public sources
(funding-agency scales, union contracts, official fellowship rates). Treat as
ranges, not point values — actual offers depend on funding source, family
situation, and local supplements.

Update annually. Sources are noted next to each entry.
"""
from __future__ import annotations

# Approximate purchasing power parity (PPP) factor: USD-equivalent of
# 1 unit of local currency for everyday spending. To convert local
# gross salary to "PPP-USD", multiply by this factor.
# Values approximated from OECD PPP data, 2024-25. Update if precision matters.
PPP_USD_PER_UNIT = {
    "USD": 1.00,
    "EUR": 1.30,    # ~1 EUR buys what $1.30 buys in the US
    "GBP": 1.50,
    "CHF": 0.95,
    "JPY": 0.0095,
    "CAD": 0.85,
    "SEK": 0.115,
    "DKK": 0.135,
    "NOK": 0.105,
    "CNY": 0.22,
    "CZK": 0.055,
    "PLN": 0.32,
    "ILS": 0.32,
    "KRW": 0.001,
    "INR": 0.029,
}


# country code -> typical entry-mid postdoc *gross annual* salary range (low, high, currency)
# These are intended as a sanity floor / ceiling; actual offers vary.
POSTDOC_SCALES = {
    # United States: DOE/NSF labs and universities. NIH NRSA FY2026 floor: $63,480
    # (NOT-OD-26-044). HEP is mostly DOE-funded; typical DOE postdoc offers
    # are $65-90k base, sometimes higher at national labs (BNL, Fermilab).
    "US": (65_000, 90_000, "USD",
           "NIH NRSA FY2026 floor $63.48k; DOE/lab offers typically higher"),

    # CERN Research Fellow (FY2026): CHF 7,004-7,425/month TAX-FREE = CHF 84-89k/year.
    # Source: https://careers.cern/jobs/research-fellowship-experimental-physics/
    # PSI/ETH-EPFL Swiss universities pay similar nominal but with tax.
    "CH": (84_000, 115_000, "CHF",
           "CERN Fellow CHF 7004-7425/month tax-free; PSI/ETH/EPFL CHF 90-115k pre-tax"),

    # Germany: TV-L E13 100% scale, postdoc typically Stufe 2-4. 2025 gross
    # roughly €56k entry, €70k senior. Some pos.: TVöD or DFG project rates.
    "DE": (56_000, 70_000, "EUR",
           "TV-L E13 100%, Stufe 2-4 (2025 scale)"),

    # France: CNRS/IN2P3 postdoc contracts. Net ~€2400-3500/month, gross
    # roughly €40-55k. https://emploi.cnrs.fr postdoc grids.
    "FR": (40_000, 55_000, "EUR",
           "CNRS/IN2P3 contract postdoc"),

    # UK: STFC PDRA Grade 7, points 30-36 approx. £36-46k 2024-25.
    "GB": (38_000, 50_000, "GBP",
           "UKRI/STFC Grade 7 PDRA; London weighting adds £3-4k"),

    # Italy: INFN Assegno di Ricerca - €25-30k net, gross ~€32-40k.
    "IT": (32_000, 42_000, "EUR",
           "INFN Assegno di Ricerca; type A vs B differs"),

    # Netherlands: postdoc CAO universities, schaal 10/11. €45-60k gross + 8% holiday.
    "NL": (47_000, 62_000, "EUR",
           "CAO Universiteiten schaal 10-11; Nikhef adds modest supplement"),

    # Spain: Juan de la Cierva, Ramón y Cajal, La Caixa. €27-40k gross.
    "ES": (28_000, 40_000, "EUR",
           "Juan de la Cierva / La Caixa / IFAE-IFIC scales"),

    # Sweden: ~SEK 38-48k/month gross.
    "SE": (480_000, 600_000, "SEK",
           "Postdoctor contract, typical 2024 levels"),

    # Denmark: Postdoc løntrin 4-8 + tillæg, DKK 38-46k/month.
    "DK": (480_000, 580_000, "DKK",
           "AC overenskomst, postdoc løntrin 4-8"),

    # Norway: SKO 1352 postdoctor, ~NOK 600-720k.
    "NO": (600_000, 730_000, "NOK",
           "SKO 1352 postdoctor"),

    # Switzerland (non-CERN): ETH/EPFL/PSI universities ~CHF 85-115k.
    # (Same as CERN key for simplicity.)

    # Japan: JSPS Standard ¥362k/month + research grant; KEK postdoc similar.
    "JP": (4_300_000, 5_500_000, "JPY",
           "JSPS Standard / KEK postdoc; +¥1.5M research grant common"),

    # China: TYC / IHEP / Tsinghua: wide range. CNY 200-450k + housing.
    "CN": (250_000, 450_000, "CNY",
           "Wide variation; TYC and Hundred Talents top out higher"),

    # Canada: NSERC PDF CAD 45-70k; university supplements typical.
    "CA": (55_000, 75_000, "CAD",
           "NSERC PDF + institutional supplement"),

    # Czech Republic: ~CZK 50-75k/month gross at major institutes.
    "CZ": (600_000, 900_000, "CZK",
           "Charles U / FZU AS CR ranges"),

    # Poland: NCN postdoc 12-18k PLN/month.
    "PL": (170_000, 240_000, "PLN",
           "NCN POLONEZ / postdoc grants"),

    # Austria: FWF ~€59-68k gross.
    "AT": (58_000, 68_000, "EUR",
           "FWF Lise Meitner / project postdoc"),

    # Israel: ~ILS 12-18k/month + housing.
    "IL": (180_000, 280_000, "ILS",
           "Mostly fellowship-based, supplemented by host"),

    # Korea: KRW 45-70M.
    "KR": (45_000_000, 70_000_000, "KRW",
           "IBS / university postdoc"),

    # India: TIFR/IISc ~INR 70-90k/month.
    "IN": (840_000, 1_400_000, "INR",
           "DAE/DST/UGC scales; major institutes pay better"),
}


# Numbeo-style cost-of-living index, anchored at NYC=100. Used only as a sanity
# check — replace with live API if you want better precision.
COL_INDEX_BY_CITY = {
    "Zurich": 117, "Geneva": 110, "Lausanne": 105,
    "New York": 100, "Boston": 90, "San Francisco": 105,
    "Los Angeles": 80, "Chicago": 72, "Princeton": 80,
    "Berkeley": 95, "Stanford": 100,
    "London": 79, "Oxford": 70, "Cambridge UK": 70, "Edinburgh": 60,
    "Paris": 70, "Lyon": 55, "Marseille": 55,
    "Munich": 70, "Berlin": 60, "Hamburg": 62, "Heidelberg": 65,
    "Amsterdam": 76, "Utrecht": 65,
    "Copenhagen": 84, "Stockholm": 72, "Oslo": 85,
    "Rome": 60, "Milan": 65, "Bologna": 58,
    "Madrid": 56, "Barcelona": 60,
    "Tokyo": 62, "Osaka": 55,
    "Beijing": 58, "Shanghai": 65,
    "Seoul": 70,
    "Tel Aviv": 80, "Jerusalem": 70,
    "Toronto": 75, "Vancouver": 78,
    "Prague": 50, "Warsaw": 50, "Krakow": 45,
    "Vienna": 65,
    "Mumbai": 38, "Bangalore": 35,
}

# Fallback per-country cost index if city not found.
COL_INDEX_BY_COUNTRY = {
    "US": 85, "CH": 110, "GB": 70, "FR": 65, "DE": 65, "NL": 70,
    "IT": 58, "ES": 55, "SE": 70, "DK": 80, "NO": 82, "FI": 70,
    "JP": 60, "CN": 55, "KR": 70, "CA": 72, "AT": 65, "CZ": 50,
    "PL": 48, "IL": 75, "IN": 36,
}


def to_ppp_usd(amount: float, currency: str) -> float | None:
    factor = PPP_USD_PER_UNIT.get(currency)
    if factor is None:
        return None
    return amount * factor


# Approximate effective tax + mandatory social-security rate on a single postdoc
# gross income. Includes income tax + employee social contributions where they
# come out of the headline salary. Sources: OECD Taxing Wages 2024 single-no-children
# tables; PWC country guides. Used only for net-salary estimation in the digest.
EFFECTIVE_TAX_RATE = {
    "US": 0.27, "DE": 0.34, "FR": 0.25, "GB": 0.23, "CH": 0.13,
    "IT": 0.31, "NL": 0.30, "ES": 0.23, "SE": 0.32, "DK": 0.36,
    "NO": 0.30, "JP": 0.20, "CN": 0.15, "CA": 0.27, "IL": 0.22,
    "IN": 0.10, "KR": 0.18, "AT": 0.32, "BE": 0.34, "PL": 0.25,
    "CZ": 0.22, "MX": 0.18, "SG": 0.10, "AU": 0.27, "BR": 0.20,
    "RU": 0.13, "IE": 0.27, "FI": 0.32, "PT": 0.27, "GR": 0.27,
    "TR": 0.20, "TW": 0.13,
}


def net_after_tax(amount: float, country: str, *, is_cern: bool = False) -> float | None:
    """Estimate net income from gross. CERN stipend is tax-free; pass is_cern=True."""
    if amount is None:
        return None
    if is_cern:
        return amount
    rate = EFFECTIVE_TAX_RATE.get(country)
    if rate is None:
        return None
    return amount * (1 - rate)


# Typical annual cost of a comfortable single-postdoc lifestyle in local currency.
# Single person, ~1BR rent + utilities + food + transport + health insurance,
# in a typical university city in that country (not the most expensive metro).
# Sources: Numbeo single-person estimates × 12, cross-checked against
# postdoc-association cost-of-living guides where available.
# Update annually. Affordability ratio = salary_low / this number.
ANNUAL_LIVING_COST_LOCAL = {
    # country  ->  (annual cost in local currency, currency)
    "US": (42_000, "USD"),     # ~$3.5k/month outside SF/NYC
    "CH": (54_000, "CHF"),     # Geneva is expensive; ~CHF 4.5k/month
    "DE": (24_000, "EUR"),     # Munich higher, Berlin/Heidelberg ~€2k/month
    "FR": (24_000, "EUR"),     # Paris ~€2.5k, rest of FR ~€1.8k
    "GB": (24_000, "GBP"),     # London £2.5k+; rest ~£1.5-1.8k
    "IT": (18_000, "EUR"),     # Rome/Milan ~€1.7k, smaller cities less
    "NL": (24_000, "EUR"),     # Amsterdam €2.5k, others ~€1.8-2k
    "ES": (16_000, "EUR"),     # Madrid/Barcelona ~€1.5k
    "SE": (240_000, "SEK"),    # SEK 20k/month outside Stockholm
    "DK": (240_000, "DKK"),    # Copenhagen ~DKK 20k
    "NO": (300_000, "NOK"),    # Oslo expensive ~NOK 25k
    "JP": (2_400_000, "JPY"),  # Tokyo ¥200k/month; Tsukuba/Tsukuba less
    "CN": (90_000, "CNY"),     # Beijing/Shanghai ~CNY 7.5k/month
    "CA": (38_000, "CAD"),     # Vancouver expensive; rest ~CAD 3k
    "AT": (22_000, "EUR"),     # Vienna ~€1.8k
    "CZ": (240_000, "CZK"),    # Prague ~CZK 20k/month
    "PL": (60_000, "PLN"),     # Warsaw/Krakow ~PLN 5k
    "IL": (130_000, "ILS"),    # Tel Aviv expensive ~ILS 11k
    "KR": (25_000_000, "KRW"), # Seoul ~KRW 2.1M
    "IN": (450_000, "INR"),    # major metros ~INR 38k
}


def affordability_ratio(salary_low: float, salary_high: float,
                        salary_currency: str, country: str) -> tuple[float, float] | None:
    """Return (low, high) ratio of postdoc gross salary / typical annual living cost.

    >1 means the salary covers basic life with surplus. >1.5 is comfortable.
    Note: doesn't account for CH/CERN tax exemption -- the CH baseline above is
    pre-tax-equivalent, so CERN's actual ratio is even better than computed.
    """
    if country not in ANNUAL_LIVING_COST_LOCAL:
        return None
    cost, cost_cur = ANNUAL_LIVING_COST_LOCAL[country]
    if cost_cur != salary_currency:
        # Try PPP-converting both to USD and dividing
        sal_low_usd = to_ppp_usd(salary_low, salary_currency)
        sal_high_usd = to_ppp_usd(salary_high, salary_currency)
        cost_usd = to_ppp_usd(cost, cost_cur)
        if not (sal_low_usd and sal_high_usd and cost_usd):
            return None
        return (sal_low_usd / cost_usd, sal_high_usd / cost_usd)
    return (salary_low / cost, salary_high / cost)
