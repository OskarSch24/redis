"""Indeed adapter — multi-country scraping (DE, AT, UK, MT)."""

from __future__ import annotations

import logging
import urllib.parse
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

COUNTRY_CONFIGS = {
    "DE": {"base": "https://de.indeed.com", "lang": "de"},
    "AT": {"base": "https://at.indeed.com", "lang": "de"},
    "UK": {"base": "https://uk.indeed.com", "lang": "en"},
    "MT": {"base": "https://mt.indeed.com", "lang": "en"},
    "NL": {"base": "https://nl.indeed.com", "lang": "en"},
}


class IndeedAdapter(BaseAdapter):
    name = "indeed"
    rate_limit_delay = 3.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
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
