"""MeetFrank adapter — uses public job API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://api.meetfrank.com/api/v2/jobs"


class MeetFrankAdapter(BaseAdapter):
    name = "meetfrank"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success"]

        for query in search_terms:
            resp = await self._get(API_URL, params={"q": query, "limit": 100})
            if not resp:
                continue
            try:
                data = resp.json()
            except Exception:
                continue

            raw_jobs = data if isinstance(data, list) else (data.get("data") or data.get("jobs") or [])
            jobs = [j for j in raw_jobs if isinstance(j, dict)] if isinstance(raw_jobs, list) else []

            for job in jobs:
                try:
                    postings.append(self._parse(job))
                except Exception as e:
                    logger.debug("[meetfrank] Parse error: %s", e)
            await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[meetfrank] Fetched %d unique jobs", len(unique))
        return unique

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        url = job.get("url") or job.get("apply_url") or job.get("link") or ""
        if not url and job.get("id"):
            url = f"https://meetfrank.com/job/{job['id']}"

        return JobPosting(
            source=self.name,
            company=job.get("company", {}).get("name", "") if isinstance(job.get("company"), dict) else str(job.get("company", "")),
            title=job.get("title") or job.get("name") or "",
            location=job.get("location", ""),
            remote_type=self._normalize_remote_type(str(job.get("remote", "")) + str(job.get("location", ""))),
            country=job.get("country", ""),
            employment_type="full_time",
            description_text=job.get("description", ""),
            application_url=url,
            raw_data=job,
        )
