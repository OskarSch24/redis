"""XING adapter — DACH-Spezialist (Germany/Austria/Switzerland professional network).

Pure-Apify adapter: XING aggressively rate-limits direct public scraping,
so there's no useful fallback. If no token is configured, the adapter
returns 0 jobs and logs a warning (which the run.py 0-result guard surfaces).

Uses shahidirfan/Xing-Jobs-Scraper (~$0.0012/job, 99.8% success).
"""

from __future__ import annotations

import logging
from datetime import date as dt_date
from typing import Any

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)


class XingAdapter(BaseAdapter):
    name = "xing"
    rate_limit_delay = 2.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if not self.apify.is_ready(self.name):
            logger.warning(
                "[xing] No Apify token — skipping (XING blocks direct scraping; "
                "set apify.actors.xing in config to enable)."
            )
            return []

        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["Berlin"]

        actor_id = self.apify.actor_id(self.name)
        postings: list[JobPosting] = []

        for query in search_terms:
            for location in search_locations:
                actor_input: dict[str, Any] = {
                    "keyword": query,
                    "location": location,
                    "results_wanted": self.apify.max_results,
                    "max_pages": 10,
                }
                logger.info(
                    "[xing] Apify: %r in %r via %s",
                    query, location, actor_id,
                )
                items = await self.apify.run_sync(actor_id, actor_input)
                logger.info(
                    "[xing] Apify returned %d items for %r/%r",
                    len(items), query, location,
                )
                country = self._infer_country(location)
                for item in items:
                    try:
                        posting = self._parse_apify_item(item, country)
                        if posting:
                            postings.append(posting)
                    except Exception as e:
                        logger.debug("[xing] Apify parse error: %s", e)

        # De-dup by URL
        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[xing] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], country: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("xingUrl")
            or ""
        )
        if not url:
            return None

        title = item.get("title") or item.get("jobTitle") or item.get("position") or ""

        company = item.get("company") or item.get("companyName") or item.get("employer") or ""
        if isinstance(company, dict):
            company = company.get("name") or company.get("title") or ""

        location = item.get("location") or item.get("city") or item.get("jobLocation") or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        description = item.get("description") or item.get("jobDescription") or ""

        salary_min = None
        salary_max = None
        salary = item.get("salary") or item.get("salaryRange")
        if isinstance(salary, dict):
            salary_min = salary.get("min") or salary.get("from")
            salary_max = salary.get("max") or salary.get("to")

        emp_raw = (item.get("employmentType") or item.get("contractType") or "").lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "freelance" in emp_raw or "contract" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        posted = None
        for key in ("postedDate", "publishedAt", "datePosted", "createdAt"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    continue

        raw = {"apify": True, "actor": self.apify.actor_id(self.name) or "shahidirfan/Xing-Jobs-Scraper"}
        for k in ("discipline", "category", "industry", "workType"):
            if k in item:
                raw[k] = item[k]

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location} {description[:500]}"),
            country=country,
            employment_type=employment,
            description_text=description[:5000] if description else "",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )

    def _infer_country(self, location: str) -> str:
        loc = (location or "").lower()
        # XING is primarily DACH; map common cities/regions
        de_cities = {"berlin", "münchen", "munich", "hamburg", "köln", "cologne",
                     "frankfurt", "stuttgart", "düsseldorf", "leipzig", "germany",
                     "deutschland"}
        at_cities = {"wien", "vienna", "graz", "linz", "salzburg", "austria",
                     "österreich"}
        ch_cities = {"zürich", "zurich", "basel", "bern", "geneva", "genf",
                     "switzerland", "schweiz"}
        if any(c in loc for c in de_cities):
            return "DE"
        if any(c in loc for c in at_cities):
            return "AT"
        if any(c in loc for c in ch_cities):
            return "CH"
        return ""
