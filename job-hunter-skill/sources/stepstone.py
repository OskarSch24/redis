"""StepStone adapter — German/European job board (HTML scraping)."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.stepstone.de"
SEARCH_URL = "https://www.stepstone.de/jobs/{query}/in-{location}/"


class StepStoneAdapter(BaseAdapter):
    name = "stepstone"
    rate_limit_delay = 3.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["deutschland"]

        for query in search_terms:
            for location in search_locations:
                encoded_q = urllib.parse.quote(query.replace(" ", "-"))
                encoded_l = urllib.parse.quote(location.lower().replace(" ", "-"))
                url = f"{BASE_URL}/jobs/{encoded_q}/in-{encoded_l}/"

                page = 1
                while page <= 5:
                    page_url = url if page == 1 else f"{url}?page={page}"
                    resp = await self._get(page_url)
                    if not resp:
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "article[data-at='job-item'], "
                        ".res-1r46nm7, "
                        "[class*='ResultItem']"
                    )

                    if not cards:
                        break

                    for card in cards:
                        try:
                            posting = self._parse_card(card)
                            if posting:
                                postings.append(posting)
                        except Exception as e:
                            logger.debug("[stepstone] Parse error: %s", e)

                    page += 1
                    await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[stepstone] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any) -> JobPosting | None:
        link = card.select_one("a[data-at='job-item-title'], a[href*='/stellenangebote--']")
        if not link:
            link = card.select_one("h2 a, h3 a")
        if not link:
            return None

        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        title = link.get_text(strip=True)

        company_el = card.select_one(
            "[data-at='job-item-company-name'], "
            ".res-nehv70, "
            "[class*='CompanyName']"
        )
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(
            "[data-at='job-item-location'], "
            "[class*='Location']"
        )
        location = location_el.get_text(strip=True) if location_el else "Deutschland"

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location}"),
            country="DE",
            employment_type="full_time",
            application_url=url,
            raw_data={"href": href},
        )
