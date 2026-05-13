"""Glassdoor adapter — scrapes public job search results."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.glassdoor.com"
SEARCH_URL = "https://www.glassdoor.com/Job/jobs.htm"


class GlassdoorAdapter(BaseAdapter):
    name = "glassdoor"
    rate_limit_delay = 4.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
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
