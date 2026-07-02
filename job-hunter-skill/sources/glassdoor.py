"""Glassdoor adapter — Apify actor with direct-HTML-scrape fallback.

Preferred path: valig/glassdoor-jobs-scraper via Apify (handles Cloudflare,
returns structured JSON incl. salary + rating). Falls back to scraping the
public search results HTML when no Apify token/actor is configured.
"""

from __future__ import annotations

import logging
import re
from datetime import date as dt_date
from datetime import timedelta
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.glassdoor.com"
SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm"

# Glassdoor pay.period → JobPosting.salary_period
_PERIOD_MAP = {
    "ANNUAL": "yearly",
    "YEARLY": "yearly",
    "MONTHLY": "monthly",
    "WEEKLY": "weekly",
    "DAILY": "daily",
    "HOURLY": "hourly",
}


class GlassdoorAdapter(BaseAdapter):
    name = "glassdoor"
    rate_limit_delay = 4.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if self.apify.is_ready(self.name):
            return await self._fetch_via_apify(queries, locations)
        logger.info("[glassdoor] Apify nicht konfiguriert — direkter Scrape-Fallback")
        return await self._fetch_direct(queries, locations)

    # ------------------------------------------------------------------ Apify

    async def _fetch_via_apify(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["Germany"]

        actor_id = self.apify.actor_id(self.name)
        postings: list[JobPosting] = []

        for query in search_terms:
            for location in search_locations:
                actor_input: dict[str, Any] = {
                    "keywords": query,
                    "location": location,
                    "limit": self.apify.max_results,
                    "daysOld": 30,
                }
                logger.info(
                    "[glassdoor] Apify: %r in %r via %s",
                    query, location, actor_id,
                )
                items = await self.apify.run_sync(actor_id, actor_input)
                logger.info(
                    "[glassdoor] Apify returned %d items for %r/%r",
                    len(items), query, location,
                )
                country = self._infer_country(location)
                for item in items:
                    try:
                        posting = self._parse_apify_item(item, country)
                        if posting:
                            postings.append(posting)
                    except Exception as e:
                        logger.debug("[glassdoor] Apify parse error: %s", e)

        # De-dup by URL
        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[glassdoor] Fetched %d unique jobs via Apify", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], hint: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("seoUrl")
            or item.get("jobUrl")
            or item.get("link")
            or ""
        )
        if not url:
            return None
        if not url.startswith("http"):
            url = f"{BASE_URL}{url}"

        title = item.get("title") or item.get("jobTitle") or item.get("position") or ""

        company = item.get("employer") or item.get("company") or item.get("companyName") or ""
        if isinstance(company, dict):
            company = company.get("name") or company.get("title") or ""

        location = item.get("location") or item.get("city") or item.get("jobLocation") or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        description = item.get("description") or item.get("jobDescription") or ""
        if "<" in description:
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s{2,}", " ", description).strip()

        salary_min = None
        salary_max = None
        salary_currency = None
        salary_period = None
        pay = item.get("pay") or item.get("salary") or item.get("salaryRange")
        if isinstance(pay, dict):
            salary_min = pay.get("min") or pay.get("from")
            salary_max = pay.get("max") or pay.get("to")
            salary_currency = pay.get("currency")
            period_raw = str(pay.get("period") or "").upper()
            salary_period = _PERIOD_MAP.get(period_raw)

        emp_raw = (item.get("employmentType") or item.get("contractType") or "").lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "freelance" in emp_raw or "contract" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        posted = None
        age = item.get("ageInDays")
        if isinstance(age, (int, float)) and age >= 0:
            posted = dt_date.today() - timedelta(days=int(age))
        if posted is None:
            for key in ("postedDate", "publishedAt", "datePosted", "createdAt"):
                val = item.get(key)
                if isinstance(val, str) and val:
                    try:
                        posted = dt_date.fromisoformat(val[:10])
                        break
                    except (ValueError, TypeError):
                        continue

        raw: dict[str, Any] = {"apify": True, "actor": "valig/glassdoor-jobs-scraper"}
        for k in ("rating", "easyApply", "ageInDays", "seoUrl"):
            if k in item:
                raw[k] = item[k]

        kwargs: dict[str, Any] = {}
        if salary_currency:
            kwargs["salary_currency"] = str(salary_currency)
        if salary_period:
            kwargs["salary_period"] = salary_period

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location} {description[:500]}"),
            country=hint,
            employment_type=employment,
            description_text=description[:5000] if description else "",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
            **kwargs,
        )

    def _infer_country(self, location: str) -> str:
        loc = (location or "").lower()
        de_terms = {"berlin", "münchen", "munich", "hamburg", "köln", "cologne",
                    "frankfurt", "stuttgart", "düsseldorf", "leipzig", "germany",
                    "deutschland"}
        at_terms = {"wien", "vienna", "graz", "linz", "salzburg", "austria",
                    "österreich"}
        ch_terms = {"zürich", "zurich", "basel", "bern", "geneva", "genf",
                    "switzerland", "schweiz"}
        nl_terms = {"amsterdam", "rotterdam", "utrecht", "netherlands", "niederlande"}
        uk_terms = {"london", "manchester", "united kingdom", "uk", "england"}
        mt_terms = {"malta", "valletta", "sliema"}
        if any(c in loc for c in de_terms):
            return "DE"
        if any(c in loc for c in at_terms):
            return "AT"
        if any(c in loc for c in ch_terms):
            return "CH"
        if any(c in loc for c in nl_terms):
            return "NL"
        if any(c in loc for c in uk_terms):
            return "UK"
        if any(c in loc for c in mt_terms):
            return "MT"
        return ""

    # ----------------------------------------------------- Direct scrape (fallback)

    async def _fetch_direct(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:2] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["Germany"]

        for query in search_terms:
            for location in search_locations:
                page = 1
                while page <= 5:
                    params = {
                        "sc.keyword": query,
                        "locT": "N",
                        "locId": self._location_id(location),
                        "p": page,
                    }

                    resp = await self._get(SEARCH_URL, params=params)
                    if not resp:
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "li[data-test='jobListing'], "
                        "[class*='jobCard'], "
                        ".react-job-listing"
                    )

                    if not cards:
                        break

                    for card in cards:
                        try:
                            posting = self._parse_card(card, location)
                            if posting:
                                postings.append(posting)
                        except Exception as e:
                            logger.debug("[glassdoor] Parse error: %s", e)

                    page += 1
                    await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[glassdoor] Fetched %d unique jobs", len(unique))
        return unique

    def _location_id(self, location: str) -> str:
        ids = {
            "germany": "94",
            "austria": "96",
            "switzerland": "150",
            "malta": "111",
            "netherlands": "178",
            "uk": "7",
        }
        return ids.get(location.lower(), "94")

    def _parse_card(self, card: Any, search_location: str) -> JobPosting | None:
        link = card.select_one("a[href*='/job-listing/'], a[href*='/partner/'], a")
        if not link:
            return None

        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        title_el = card.select_one(
            "[data-test='job-title'], "
            "[class*='JobCard_jobTitle'], "
            "a.jobLink"
        )
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        company_el = card.select_one(
            "[data-test='employer-name'], "
            "[class*='EmployerProfile_compactEmployerName']"
        )
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(
            "[data-test='emp-location'], "
            "[class*='JobCard_location']"
        )
        location = location_el.get_text(strip=True) if location_el else search_location

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location}"),
            country="",
            employment_type="full_time",
            application_url=url,
            raw_data={"search_location": search_location},
        )
