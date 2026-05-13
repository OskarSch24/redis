"""Arbeitnow adapter — uses official open API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowAdapter(BaseAdapter):
    name = "arbeitnow"
    rate_limit_delay = 1.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        page = 1
        seen_slugs: set[str] = set()

        while True:
            resp = await self._get(API_URL, params={"page": page})
            if not resp:
                break
            try:
                data = resp.json()
            except Exception:
                break

            jobs = data.get("data", [])
            if not jobs:
                break

            for job in jobs:
                slug = job.get("slug", "")
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                title = job.get("title", "")
                if not self._title_relevant(title, queries):
                    continue

                postings.append(self._parse(job))

            if not data.get("links", {}).get("next"):
                break
            page += 1
            if page > 50:
                break
            await self._sleep()

        logger.info("[arbeitnow] Fetched %d jobs", len(postings))
        return postings

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        tags = job.get("tags", [])
        remote = "full_remote" if job.get("remote") else "onsite"

        return JobPosting(
            source=self.name,
            company=job.get("company_name", ""),
            title=job.get("title", ""),
            location=job.get("location", ""),
            remote_type=remote,
            country="DE",
            employment_type="full_time",
            description_text=job.get("description", ""),
            application_url=job.get("url", ""),
            skills_mentioned=tags,
            raw_data=job,
        )
