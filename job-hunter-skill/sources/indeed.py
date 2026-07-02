"""Indeed adapter — multi-country scraping (DE, AT, UK, MT).

Hybrid adapter: uses misceres/indeed-scraper via Apify when a token and
actor are configured (deactivated by default in config — cost driver),
otherwise falls back to direct HTML scraping of the country portals.
"""

from __future__ import annotations

import logging
import re
from datetime import date as dt_date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

COUNTRY_CONFIGS = {
    "DE": {"base": "https://de.indeed.com", "lang": "de"},
    "AT": {"base": "https://at.indeed.com", "lang": "de"},
    "UK": {"base": "https://uk.indeed.com", "lang": "en"},
    "MT": {"base": "https://mt.indeed.com", "lang": "en"},
    "NL": {"base": "https://nl.indeed.com", "lang": "en"},
}

# misceres/indeed-scraper expects two-letter ISO country codes ("GB", not "UK")
INDEED_APIFY_COUNTRY = {
    "DE": "DE",
    "AT": "AT",
    "UK": "GB",
    "MT": "MT",
    "NL": "NL",
    "CH": "CH",
}


class IndeedAdapter(BaseAdapter):
    name = "indeed"
    rate_limit_delay = 3.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if self.apify.is_ready(self.name):
            return await self._fetch_via_apify(queries, locations)
        logger.info("[indeed] Apify nicht konfiguriert — direkter Scrape-Fallback")
        return await self._fetch_direct(queries, locations)

    # ------------------------------------------------------------------
    # Apify path (misceres/indeed-scraper)
    # ------------------------------------------------------------------

    async def _fetch_via_apify(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        # Cost driver: keep it to ~1 call per query, max 3 queries,
        # first configured country + first location only.
        search_terms = queries[:3] if queries else ["customer success manager"]
        target_countries = self.config.get("location", {}).get("countries", ["DE"])
        country = (target_countries[0] if target_countries else "DE").upper()
        indeed_country = INDEED_APIFY_COUNTRY.get(country, country)
        location = locations[0] if locations else ""

        actor_id = self.apify.actor_id(self.name)
        postings: list[JobPosting] = []

        for query in search_terms:
            actor_input: dict[str, Any] = {
                "position": query,
                "country": indeed_country,
                "maxItemsPerSearch": self.apify.max_results,
            }
            if location:
                actor_input["location"] = location

            logger.info(
                "[indeed] Apify: %r in %r (%s) via %s",
                query, location, indeed_country, actor_id,
            )
            items = await self.apify.run_sync(actor_id, actor_input)
            logger.info(
                "[indeed] Apify returned %d items for %r",
                len(items), query,
            )
            for item in items:
                try:
                    posting = self._parse_apify_item(item, country)
                    if posting:
                        postings.append(posting)
                except Exception as e:
                    logger.debug("[indeed] Apify parse error: %s", e)

        # De-dup by URL
        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[indeed] Fetched %d unique jobs via Apify", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], hint: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("externalApplyLink")
            or ""
        )
        if not url:
            return None

        title = item.get("positionName") or item.get("title") or item.get("jobTitle") or ""

        company = item.get("company") or item.get("companyName") or item.get("employer") or ""
        if isinstance(company, dict):
            company = company.get("name") or company.get("title") or ""

        location = item.get("location") or item.get("city") or item.get("jobLocation") or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        description = item.get("description") or item.get("jobDescription") or ""

        salary_min = None
        salary_max = None
        salary_currency = ""
        salary_period = ""
        salary = item.get("salary") or item.get("salaryRange")
        if isinstance(salary, dict):
            salary_min = salary.get("min") or salary.get("from")
            salary_max = salary.get("max") or salary.get("to")
        elif isinstance(salary, str) and salary:
            # e.g. "€45.000 - €60.000 pro Jahr", "£30,000 a year", "$25 an hour"
            nums = []
            for m in re.findall(r"\d[\d.,]*", salary):
                cleaned = re.sub(r"[.,](?=\d{3}\b)", "", m).replace(",", ".")
                try:
                    nums.append(float(cleaned))
                except ValueError:
                    continue
            if nums:
                salary_min = nums[0]
                salary_max = nums[1] if len(nums) > 1 else None
            low = salary.lower()
            if "€" in salary or "eur" in low:
                salary_currency = "EUR"
            elif "£" in salary or "gbp" in low:
                salary_currency = "GBP"
            elif "$" in salary or "usd" in low:
                salary_currency = "USD"
            if "jahr" in low or "year" in low or "annum" in low:
                salary_period = "year"
            elif "monat" in low or "month" in low:
                salary_period = "month"
            elif "stunde" in low or "hour" in low:
                salary_period = "hour"

        emp_raw = item.get("jobType") or item.get("employmentType") or item.get("contractType") or ""
        if isinstance(emp_raw, list):
            emp_raw = " ".join(str(x) for x in emp_raw)
        emp_raw = emp_raw.lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "freelance" in emp_raw or "contract" in emp_raw or "befristet" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        # postedAt is usually relative ("30+ days ago", "Just posted"),
        # but tolerate ISO dates from other actor versions too.
        posted = None
        for key in ("postedAt", "postedDate", "publishedAt", "datePosted", "createdAt"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    pass
                low = val.lower()
                rel = re.search(r"(\d+)\+?\s*(?:day|tag)", low)
                if rel:
                    posted = dt_date.today() - timedelta(days=int(rel.group(1)))
                    break
                if "today" in low or "just posted" in low or "heute" in low:
                    posted = dt_date.today()
                    break

        raw: dict[str, Any] = {"apify": True, "actor": "misceres/indeed-scraper"}
        for k in ("id", "jobType", "rating", "reviewsCount", "salary", "externalApplyLink"):
            if k in item:
                raw[k] = item[k]

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
            salary_currency=salary_currency,
            salary_period=salary_period,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )

    # ------------------------------------------------------------------
    # Direct-scrape fallback (original implementation)
    # ------------------------------------------------------------------

    async def _fetch_direct(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        config = self.config

        target_countries = config.get("location", {}).get("countries", ["DE"])
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locs = locations[:2] if locations else [""]

        for country in target_countries:
            if country not in COUNTRY_CONFIGS:
                continue
            cc = COUNTRY_CONFIGS[country]
            base_url = cc["base"]

            for query in search_terms:
                for loc in search_locs:
                    start = 0
                    while start < 100:
                        params = {
                            "q": query,
                            "start": start,
                            "fromage": "30",
                        }
                        if loc:
                            params["l"] = loc

                        resp = await self._get(f"{base_url}/jobs", params=params)
                        if not resp:
                            break

                        soup = BeautifulSoup(resp.text, "html.parser")
                        cards = soup.select(
                            ".job_seen_beacon, "
                            "[data-testid='jobsearch-ResultsList'] li, "
                            ".jobsearch-SerpJobCard"
                        )

                        if not cards:
                            break

                        for card in cards:
                            try:
                                posting = self._parse_card(card, base_url, country)
                                if posting:
                                    postings.append(posting)
                            except Exception as e:
                                logger.debug("[indeed] Parse error: %s", e)

                        start += 10
                        await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[indeed] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any, base_url: str, country: str) -> JobPosting | None:
        link = card.select_one("a[data-jk], a[id*='job_'], h2 a")
        if not link:
            return None

        href = link.get("href", "")
        job_key = link.get("data-jk", "")

        if href.startswith("/"):
            url = f"{base_url}{href}"
        elif href.startswith("http"):
            url = href
        elif job_key:
            url = f"{base_url}/viewjob?jk={job_key}"
        else:
            return None

        title_el = card.select_one(
            "h2.jobTitle span, "
            "[data-testid='jobTitle'], "
            ".jobTitle"
        )
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        company_el = card.select_one(
            "[data-testid='company-name'], "
            ".companyName, "
            "span.company"
        )
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(
            "[data-testid='text-location'], "
            ".companyLocation, "
            ".location"
        )
        location = location_el.get_text(strip=True) if location_el else ""

        salary_el = card.select_one("[data-testid='attribute_snippet_testid'], .salary-snippet")
        salary_text = salary_el.get_text(strip=True) if salary_el else ""

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location}"),
            country=country,
            employment_type=self._detect_employment_type(card),
            description_text=salary_text,
            application_url=url,
            raw_data={"country": country, "href": href},
        )

    def _detect_employment_type(self, card: Any) -> str:
        text = card.get_text(" ", strip=True).lower()
        if "part-time" in text or "teilzeit" in text:
            return "part_time"
        if "contract" in text or "freelance" in text:
            return "contract"
        return "full_time"
