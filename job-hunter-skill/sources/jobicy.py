"""Jobicy adapter — uses official API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://jobicy.com/api/v2/remote-jobs"


class JobicyAdapter(BaseAdapter):
    name = "jobicy"
    rate_limit_delay = 1.5

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries if queries else [""]

        for query in search_terms[:5]:
            params: dict[str, Any] = {"count": 50}
            if query:
                params["tag"] = query.split()[0]

            resp = await self._get(API_URL, params=params)
            if not resp:
                continue
            try:
                data = resp.json()
            except Exception:
                continue

            for job in data.get("jobs", []):
                if not self._title_relevant(job.get("jobTitle", ""), queries):
                    continue
                postings.append(self._parse(job))
            await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[jobicy] Fetched %d unique jobs", len(unique))
        return unique

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        return JobPosting(
            source=self.name,
            company=job.get("companyName", ""),
            title=job.get("jobTitle", ""),
            location=job.get("jobGeo", "Worldwide"),
            remote_type="full_remote",
            country="",
            employment_type=self._map_type(job.get("jobType", "")),
            description_text=job.get("jobDescription", ""),
            application_url=job.get("url", ""),
            skills_mentioned=job.get("jobIndustry", []) if isinstance(job.get("jobIndustry"), list) else [],
            raw_data=job,
        )

    def _map_type(self, jtype: str) -> str:
        lower = jtype.lower()
        if "part" in lower:
            return "part_time"
        if "contract" in lower or "freelance" in lower:
            return "contract"
        return "full_time"
