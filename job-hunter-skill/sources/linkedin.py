"""LinkedIn adapter — Apify actor with direct public-search fallback.

Preferred path: harvestapi/linkedin-job-search via Apify (accepts multiple
job titles AND locations in a single input → ~1 call per run).
Fallback: the original jobs-guest HTML scrape (rate-limited, no login).
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import date as dt_date
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
JOBS_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

APIFY_ACTOR = "harvestapi/linkedin-job-search"


class LinkedInAdapter(BaseAdapter):
    name = "linkedin"
    rate_limit_delay = 4.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if self.apify.is_ready(self.name):
            return await self._fetch_via_apify(queries, locations)
        logger.info("[linkedin] Apify nicht konfiguriert — direkter Scrape-Fallback")
        return await self._fetch_direct(queries, locations)

    # ------------------------------------------------------------------ Apify

    async def _fetch_via_apify(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:3] if locations else ["Germany", "Europe"]

        actor_id = self.apify.actor_id(self.name)

        # harvestapi/linkedin-job-search accepts arrays for both job titles
        # and locations — one call covers the whole search matrix.
        actor_input: dict[str, Any] = {
            "jobTitles": search_terms,
            "locations": search_locations,
            "maxItems": self.apify.max_results,
            "postedLimit": "month",  # mirrors the direct scrape's last-30-days filter
        }

        logger.info(
            "[linkedin] Apify: %d keywords x %d locations via %s",
            len(search_terms), len(search_locations), actor_id,
        )
        items = await self.apify.run_sync(actor_id, actor_input)
        logger.info("[linkedin] Apify returned %d items", len(items))

        postings: list[JobPosting] = []
        for item in items:
            try:
                posting = self._parse_apify_item(item, "")
                if posting:
                    postings.append(posting)
            except Exception as e:
                logger.debug("[linkedin] Apify parse error: %s", e)

        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[linkedin] Fetched %d unique jobs via Apify", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], hint: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("linkedinUrl")
            or item.get("applyUrl")
            or ""
        )
        if not url:
            return None

        title = item.get("title") or item.get("jobTitle") or item.get("position") or ""

        company = ""
        org = (
            item.get("companyName")
            or item.get("company")
            or item.get("companyDetails")
            or item.get("employer")
        )
        if isinstance(org, dict):
            company = org.get("name") or org.get("universalName") or org.get("title") or ""
        elif isinstance(org, str):
            company = org

        location = ""
        loc = item.get("location") or item.get("jobLocation") or item.get("city")
        if isinstance(loc, dict):
            location = (
                loc.get("linkedinText")
                or loc.get("text")
                or loc.get("name")
                or loc.get("city")
                or ""
            )
        elif isinstance(loc, str):
            location = loc

        description = (
            item.get("descriptionText")
            or item.get("description")
            or item.get("jobDescription")
            or ""
        )
        if not isinstance(description, str):
            description = ""

        salary_min = None
        salary_max = None
        salary = item.get("salary") or item.get("salaryRange") or item.get("payRange")
        if isinstance(salary, dict):
            salary_min = salary.get("min") or salary.get("from") or salary.get("minimum")
            salary_max = salary.get("max") or salary.get("to") or salary.get("maximum")

        emp_raw = item.get("employmentType") or item.get("contractType") or ""
        if isinstance(emp_raw, list):
            emp_raw = " ".join(str(e) for e in emp_raw)
        emp_raw = str(emp_raw).lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "contract" in emp_raw or "freelance" in emp_raw or "temporary" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        workplace = item.get("workplaceType") or item.get("workplaceTypes") or ""
        if isinstance(workplace, list):
            workplace = " ".join(str(w) for w in workplace)
        workplace = str(workplace).lower()
        if "remote" in workplace:
            remote_signal = "fully remote"
        elif "hybrid" in workplace:
            remote_signal = "hybrid"
        elif "office" in workplace or "on-site" in workplace:
            remote_signal = "onsite"
        else:
            remote_signal = ""

        posted = None
        for key in ("postedDate", "postedAt", "publishedAt", "listedAt", "datePosted"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    continue

        raw: dict[str, Any] = {"apify": True, "actor": APIFY_ACTOR}
        for k in ("workplaceType", "employmentType", "experienceLevel",
                  "applicantsCount", "easyApply", "industries"):
            if k in item:
                raw[k] = item[k]
        if isinstance(salary, str) and salary:
            raw["salary_text"] = salary

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(
                f"{remote_signal} {title} {location} {description[:500]}"
            ),
            country=self._infer_country(location or hint),
            employment_type=employment,
            description_text=description[:5000] if description else "",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )

    # ----------------------------------------------------- Direct scrape (fallback)

    async def _fetch_direct(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["Germany", "Europe"]

        for query in search_terms:
            for location in search_locations:
                start = 0
                while start < 100:
                    params = {
                        "keywords": query,
                        "location": location,
                        "start": start,
                        "f_TPR": "r2592000",  # last 30 days
                    }

                    resp = await self._get(JOBS_URL, params=params)
                    if not resp:
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(".base-card, .job-search-card")

                    if not cards:
                        break

                    for card in cards:
                        try:
                            posting = self._parse_card(card, location)
                            if posting:
                                postings.append(posting)
                        except Exception as e:
                            logger.debug("[linkedin] Parse error: %s", e)

                    start += 25
                    await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[linkedin] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any, search_location: str) -> JobPosting | None:
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        if not link:
            return None
        url = link.get("href", "").split("?")[0]
        if not url:
            return None

        title_el = card.select_one(
            "h3.base-search-card__title, "
            ".job-search-card__title, "
            "h3"
        )
        title = title_el.get_text(strip=True) if title_el else ""

        company_el = card.select_one(
            "h4.base-search-card__subtitle, "
            ".job-search-card__company-name, "
            "a[data-tracking-control-name*='company']"
        )
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(
            ".job-search-card__location, "
            "span.job-result-card__location"
        )
        location = location_el.get_text(strip=True) if location_el else search_location

        date_el = card.select_one("time")
        posted = None
        if date_el:
            datetime_attr = date_el.get("datetime", "")
            if datetime_attr:
                try:
                    posted = dt_date.fromisoformat(datetime_attr[:10])
                except ValueError:
                    pass

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location}"),
            country=self._infer_country(location),
            employment_type="full_time",
            posted_date=posted,
            application_url=url,
            raw_data={"search_location": search_location},
        )

    def _infer_country(self, location: str) -> str:
        mapping = {
            "germany": "DE",
            "deutschland": "DE",
            "austria": "AT",
            "österreich": "AT",
            "switzerland": "CH",
            "malta": "MT",
            "netherlands": "NL",
            "uk": "UK",
            "united kingdom": "UK",
        }
        lower = location.lower()
        for key, code in mapping.items():
            if key in lower:
                return code
        return ""
