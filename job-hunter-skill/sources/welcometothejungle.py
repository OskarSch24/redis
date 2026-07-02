"""Welcome to the Jungle adapter — European tech-focused job board.

Pure-Apify adapter. WTTJ uses heavy JS, direct scraping is impractical.
Returns 0 jobs (with warning) if no Apify token is configured.

Uses silentflow/welcome-to-the-jungle-scraper-ppe (~$0.0009/job, 98.2% success).
Accepts multi-keyword search natively — one call covers all queries per language/location.
"""

from __future__ import annotations

import logging
from datetime import date as dt_date
from typing import Any

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

# Map our config country codes to WTTJ's market/language enum.
# WTTJ supports: fr, en, de, es, nl, pt, it
COUNTRY_TO_WTTJ_LANG = {
    "DE": "de",
    "AT": "de",
    "CH": "de",
    "UK": "en",
    "NL": "nl",
    "MT": "en",
    # WTTJ does not have Maltese / Greek / Polish markets
}


class WelcomeToTheJungleAdapter(BaseAdapter):
    name = "welcometothejungle"
    rate_limit_delay = 2.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if not self.apify.is_ready(self.name):
            logger.warning(
                "[wttj] No Apify token — skipping (WTTJ is JS-heavy, direct "
                "scrape not viable). Configure apify.actors.welcometothejungle."
            )
            return []

        search_terms = queries[:5] if queries else ["customer success manager"]
        target_countries = self.config.get("location", {}).get("countries", ["DE"])

        # Determine which markets to query (unique WTTJ languages from configured countries)
        wttj_langs: list[str] = []
        seen: set[str] = set()
        for cc in target_countries:
            lang = COUNTRY_TO_WTTJ_LANG.get(cc.upper())
            if lang and lang not in seen:
                seen.add(lang)
                wttj_langs.append(lang)
        if not wttj_langs:
            wttj_langs = ["en"]

        # Build remote-types filter from config
        loc_cfg = self.config.get("location", {})
        remote_types: list[str] = []
        if loc_cfg.get("remote", True):
            remote_types.append("fulltime")
        if loc_cfg.get("hybrid", True):
            remote_types.extend(["partial", "punctual"])
        if loc_cfg.get("onsite", False):
            remote_types.append("no")

        # Contract types from config
        emp_cfg = self.config.get("employment", {})
        contract_types: list[str] = []
        if emp_cfg.get("full_time", True):
            contract_types.append("full_time")
        if emp_cfg.get("part_time", True):
            contract_types.append("part_time")
        if emp_cfg.get("freelance", False):
            contract_types.append("freelance")

        actor_id = self.apify.actor_id(self.name)
        postings: list[JobPosting] = []

        for lang in wttj_langs:
            actor_input: dict[str, Any] = {
                "searches": search_terms,
                "language": lang,
                "scrapeMode": "jobs",
                "publishedSince": "month",
                "maxItems": self.apify.max_results,
            }
            if remote_types:
                actor_input["remoteTypes"] = remote_types
            if contract_types:
                actor_input["contractTypes"] = contract_types

            logger.info(
                "[wttj] Apify: %d keywords in lang=%s via %s",
                len(search_terms), lang, actor_id,
            )
            items = await self.apify.run_sync(actor_id, actor_input)
            logger.info(
                "[wttj] Apify returned %d items for lang=%s",
                len(items), lang,
            )
            for item in items:
                try:
                    posting = self._parse_apify_item(item, lang)
                    if posting:
                        postings.append(posting)
                except Exception as e:
                    logger.debug("[wttj] Apify parse error: %s", e)

        seen_urls: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen_urls:
                seen_urls.add(p.application_url)
                unique.append(p)

        logger.info("[wttj] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], lang: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("applyUrl")
            or ""
        )
        if not url:
            return None

        title = item.get("title") or item.get("name") or item.get("jobTitle") or ""

        company = ""
        org = item.get("organization") or item.get("company") or item.get("companyName")
        if isinstance(org, dict):
            company = org.get("name") or org.get("title") or ""
        elif isinstance(org, str):
            company = org

        location = ""
        loc = item.get("office") or item.get("location") or item.get("city")
        if isinstance(loc, dict):
            location = loc.get("name") or loc.get("city") or ""
        elif isinstance(loc, str):
            location = loc
        elif isinstance(loc, list) and loc:
            first = loc[0]
            if isinstance(first, dict):
                location = first.get("name") or first.get("city") or ""
            else:
                location = str(first)

        description = item.get("description") or item.get("jobDescription") or ""

        salary_min = None
        salary_max = None
        salary = item.get("salary") or item.get("salaryRange") or item.get("compensation")
        if isinstance(salary, dict):
            salary_min = salary.get("min") or salary.get("from") or salary.get("minimum")
            salary_max = salary.get("max") or salary.get("to") or salary.get("maximum")

        contract = (item.get("contractType") or item.get("employmentType") or "").lower()
        if "part" in contract:
            employment = "part_time"
        elif "freelance" in contract or "contract" in contract:
            employment = "contract"
        elif "intern" in contract or "apprentice" in contract:
            employment = "contract"
        else:
            employment = "full_time"

        remote_flag = (item.get("remote") or item.get("remoteType") or "").lower()
        if "fulltime" in remote_flag or "full" in remote_flag:
            remote_signal = "fully remote"
        elif "partial" in remote_flag or "hybrid" in remote_flag:
            remote_signal = "hybrid"
        elif remote_flag in ("no", "onsite", "office"):
            remote_signal = "onsite"
        else:
            remote_signal = ""

        posted = None
        for key in ("publishedAt", "postedDate", "createdAt", "datePosted"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    continue

        # Infer country from WTTJ market language
        country_from_lang = {
            "de": "DE",  # could be AT/CH but DE is the dominant market
            "nl": "NL",
            "en": "",   # too ambiguous
        }.get(lang, "")

        raw = {"apify": True, "actor": "silentflow/welcome-to-the-jungle-scraper-ppe",
               "wttj_lang": lang}
        for k in ("contractType", "remoteType", "experienceLevel", "tags",
                  "education", "languages"):
            if k in item:
                raw[k] = item[k]

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(
                f"{remote_signal} {title} {location} {description[:500]}"
            ),
            country=country_from_lang,
            employment_type=employment,
            description_text=description[:5000] if description else "",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )
