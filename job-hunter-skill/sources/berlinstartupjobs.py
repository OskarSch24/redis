"""BerlinStartupJobs adapter — scrapes listings."""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://berlinstartupjobs.com"
SEARCH_URL = "https://berlinstartupjobs.com/engineering-it/"


class BerlinStartupJobsAdapter(BaseAdapter):
    name = "berlinstartupjobs"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        categories = [
            "https://berlinstartupjobs.com/sales/",
            "https://berlinstartupjobs.com/customer-success/",
            "https://berlinstartupjobs.com/operations/",
            "https://berlinstartupjobs.com/account-management/",
        ]

        for cat_url in categories:
            page = 1
            while page <= 3:
                url = f"{cat_url}page/{page}/" if page > 1 else cat_url
                resp = await self._get(url)
                if not resp or resp.status_code == 404:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select(".bjs-jlid, .job-listing, article.type-job_listing")

                if not cards:
                    break

                for card in cards:
                    try:
                        posting = self._parse_card(card)
                        if posting and self._title_relevant(posting.title, queries):
                            postings.append(posting)
                    except Exception as e:
                        logger.debug("[berlinstartupjobs] Parse error: %s", e)

                page += 1
                await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[berlinstartupjobs] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any) -> JobPosting | None:
        link = card.select_one("a.bjs-jlid__h, h2 a, a")
        if not link:
            return None
        url = link.get("href", "")

        title = link.get_text(strip=True)
        company_el = card.select_one(".bjs-jlid__b, .company")
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(".bjs-jlid__meta-location, .location")
        location = location_el.get_text(strip=True) if location_el else "Berlin, DE"

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(location),
            country="DE",
            employment_type="full_time",
            application_url=url,
            raw_data={"href": url},
        )

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)
