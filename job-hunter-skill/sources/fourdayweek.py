"""4dayweek.io adapter — scrapes job listings HTML."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://4dayweek.io"
JOBS_URL = "https://4dayweek.io/jobs"


class FourDayWeekAdapter(BaseAdapter):
    name = "4dayweek"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        page = 1

        while page <= 10:
            url = f"{JOBS_URL}?page={page}" if page > 1 else JOBS_URL
            resp = await self._get(url)
            if not resp:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            job_cards = soup.select("a[href*='/job/']")

            if not job_cards:
                break

            for card in job_cards:
                try:
                    posting = self._parse_card(card)
                    if posting and self._title_relevant(posting.title, queries):
                        postings.append(posting)
                except Exception as e:
                    logger.debug("[4dayweek] Card parse error: %s", e)

            page += 1
            await self._sleep()

        logger.info("[4dayweek] Fetched %d jobs", len(postings))
        return postings

    def _parse_card(self, card: Any) -> JobPosting | None:
        href = card.get("href", "")
        if not href:
            return None
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        title_el = card.select_one("h2, h3, .job-title, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:80]

        company_el = card.select_one(".company, [class*='company']")
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(".location, [class*='location']")
        location = location_el.get_text(strip=True) if location_el else "Remote"

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type="full_remote",
            country="",
            employment_type="part_time",
            hours_per_week=32,
            application_url=url,
            raw_data={"href": href},
        )

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)
