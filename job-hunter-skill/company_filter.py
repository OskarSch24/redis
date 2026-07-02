"""Company-Größen-Filter: hartes Reject für Großkonzerne.

Drei-Stufen-Klassifikation (jede einzelne reicht für REJECT):
  1) Name-Blacklist (Substring, case-insensitive) — zuverlässigster Detektor
  2) Numerisches max_employees, falls Adapter eine MA-Zahl liefert
     (kommt in raw_data["employees"]/"size"/"companySize")
  3) Description-Signale ("10,000 employees", "global corporation", "dax 40")

Greift nach dem Geo-Filter in run.py.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schema import JobPosting


# --- Description-Indikatoren für "zu groß" ---
LARGE_COMPANY_SIGNALS: tuple[str, ...] = (
    "fortune 500", "fortune 100", "fortune 50",
    "ftse 100", "ftse 250", "dax 40", "dax 30", "tecdax",
    "global corporation", "multinational", "publicly traded",
    "börsennotiert", "stock-listed", "publicly listed",
    "10,000 employees", "10.000 employees", "10000 employees",
    "20,000 employees", "20.000 employees",
    "50,000 employees", "50.000 employees",
    "100,000 employees", "100.000 employees",
    "over 5,000 employees", "more than 5,000 employees",
    "weltweit über 5000", "über 5.000 mitarbeiter",
    "über 10.000 mitarbeiter", "more than 10000 employees",
    "fortune-500", "großkonzern", "konzern",
)


# Regex für "N employees" / "N Mitarbeiter" in Description, um max_employees zu extrahieren
_EMPLOYEE_PATTERNS = [
    re.compile(r"(\d[\d,\.]{2,})\s*\+?\s*(?:employees|mitarbeiter|people|staff|team members)", re.I),
    re.compile(r"team of (\d[\d,\.]{2,})", re.I),
    re.compile(r"a (?:team|company) of (?:over |more than )?(\d[\d,\.]{2,})", re.I),
]


def _parse_employee_count(text: str) -> int | None:
    """Try to extract a numeric employee count from free text."""
    if not text:
        return None
    for pat in _EMPLOYEE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "").replace(".", "")
            try:
                return int(raw)
            except ValueError:
                continue
    return None


def _name_matches_blacklist(company: str, blacklist: list[str]) -> str | None:
    """Return the matching blacklist entry (for logging) or None."""
    if not company:
        return None
    company_lower = company.lower().strip()
    for bad in blacklist:
        bad_lower = bad.lower().strip()
        # Exact or substring match — guard against false positives for short
        # blacklist names by requiring word boundary
        if len(bad_lower) <= 3:
            # Very short names (e.g. "SAP") — require strict word boundary
            if re.search(rf"\b{re.escape(bad_lower)}\b", company_lower):
                return bad
        else:
            if bad_lower in company_lower:
                return bad
    return None


def is_acceptable_company(job: "JobPosting", company_cfg: dict[str, Any]) -> tuple[bool, str]:
    """Klassifiziert ob die Firma im KMU-Bereich liegt.

    Returns (acceptable, reason). Reason wird in run.py geloggt.
    """
    if not company_cfg.get("enabled", True):
        return True, "filter disabled"

    blacklist = company_cfg.get("blacklist", []) or []
    max_emp = int(company_cfg.get("max_employees", 500))

    # 1) Name-Blacklist
    hit = _name_matches_blacklist(job.company, blacklist)
    if hit:
        return False, f"blacklist: {hit}"

    # 2) Numerischer MA-Wert aus raw_data (falls vorhanden)
    raw = job.raw_data or {}
    for key in ("employees", "employeesCount", "companySize", "size", "employeeCount"):
        val = raw.get(key)
        if isinstance(val, int) and val > 0:
            if val > max_emp:
                return False, f"employees={val} > {max_emp}"
            return True, f"employees={val} (within limit)"
        if isinstance(val, str):
            # Sometimes it's "1001-5000" — take the lower bound
            m = re.match(r"(\d+)", val.strip())
            if m:
                low = int(m.group(1))
                if low > max_emp:
                    return False, f"employees={val} > {max_emp}"

    # 3) Description-Signal "über 10.000 Mitarbeiter" etc.
    desc = (job.description_text or "").lower()
    for signal in LARGE_COMPANY_SIGNALS:
        if signal in desc:
            return False, f"description-signal: {signal!r}"

    # 4) Quantitative Description-Extraktion (regex)
    parsed = _parse_employee_count(job.description_text or "")
    if parsed and parsed > max_emp:
        return False, f"description sagt ~{parsed} employees > {max_emp}"

    # Default: durchlassen
    return True, "no negative signal"
