"""RemoteOK adapter — uses official API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://remoteok.com/api"


class RemoteOKAdapter(BaseAdapter):
    name = "remoteok"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        resp = await self._get(API_URL, headers={"User-Agent": "job-hunter-skill/1.0"})
        if not resp:
            return []

        try:
            raw = resp.json()
        except Exception:
            return []

        # First element is a legal notice dict, skip it
        jobs = [j for j in raw if isinstance(j, dict) and "id" in j]
        postings: list[JobPosting] = []

        for job in jobs:
            title = job.get("position", "")
            if not self._title_relevant(title, queries):
                continue
            postings.append(self._parse(job))

        logger.info("[remoteok] Fetched %d jobs", len(postings))
        return postings

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        tags = job.get("tags", [])
        return JobPosting(
            source=self.name,
            company=job.get("company", ""),
            title=job.get("position", ""),
            location=job.get("location", "Worldwide"),
            remote_type="full_remote",
            country="",
            employment_type="full_time",
            description_text=job.get("description", ""),
            application_url=job.get("url", f"https://remoteok.com/l/{job.get('slug', '')}"),
            skills_mentioned=tags if isinstance(tags, list) else [],
            raw_data=job,
        )
