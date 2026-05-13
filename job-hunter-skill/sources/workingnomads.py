"""WorkingNomads adapter — scrapes JSON API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://www.workingnomads.com/api/exposed_jobs/"


class WorkingNomadsAdapter(BaseAdapter):
    name = "workingnomads"
    rate_limit_delay = 1.5

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        resp = await self._get(API_URL)
        if not resp:
            return []

        try:
            jobs = resp.json()
        except Exception:
            return []

        postings: list[JobPosting] = []
        for job in jobs if isinstance(jobs, list) else []:
            title = job.get("title", "")
            if not self._title_relevant(title, queries):
                continue
            postings.append(self._parse(job))

        logger.info("[workingnomads] Fetched %d jobs", len(postings))
        return postings

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        return JobPosting(
            source=self.name,
            company=job.get("company_name", ""),
            title=job.get("title", ""),
            location=job.get("location", "Remote"),
            remote_type="full_remote",
            country="",
            employment_type="full_time",
            description_text=job.get("description", ""),
            application_url=job.get("url", ""),
            skills_mentioned=[job.get("category", "")] if job.get("category") else [],
            raw_data=job,
        )
