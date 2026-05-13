"""Workwide.de adapter — German remote jobs."""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://workwide.de"
JOBS_URL = "https://workwide.de/jobs"


class WorkwideAdapter(BaseAdapter):
    name = "workwide"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success"]

        for query in search_terms:
            resp = await self._get(JOBS_URL, params={"q": query})
            if not resp:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("article, .job, [class*='job-card']")

            for card in cards:
                try:
                    posting = self._parse_card(card)
                    if posting:
                        postings.append(posting)
                except Exception as e:
                    logger.debug("[workwide] Parse error: %s", e)

            await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[workwide] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any) -> JobPosting | None:
        link = card.select_one("a[href*='/job']")
        if not link:
            link = card.select_one("a")
        if not link:
            return None

        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        title_el = card.select_one("h2, h3, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        company_el = card.select_one("[class*='company'], [class*='employer']")
        company = company_el.get_text(strip=True) if company_el else ""

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location="Remote (DE)",
            remote_type="full_remote",
            country="DE",
            employment_type="full_time",
            application_url=url,
            raw_data={"href": href},
        )
