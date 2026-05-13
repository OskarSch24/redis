"""LinkedIn adapter — public job search (rate-limited, no login required)."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
JOBS_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


class LinkedInAdapter(BaseAdapter):
    name = "linkedin"
    rate_limit_delay = 4.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
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
                    from datetime import date as dt_date
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
