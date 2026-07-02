"""Remotive adapter — uses official API."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveAdapter(BaseAdapter):
    name = "remotive"
    rate_limit_delay = 1.5

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []

        search_terms = queries if queries else ["customer success"]
        for query in search_terms:
            resp = await self._get(API_URL, params={"search": query, "limit": 500})
            if not resp:
                continue
            try:
                data = resp.json()
            except Exception:
                continue

            for job in data.get("jobs", []):
                try:
                    postings.append(self._parse(job))
                except Exception as e:
                    logger.debug("[remotive] Parse error: %s", e)
            await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[remotive] Fetched %d unique jobs", len(unique))
        return unique

    def _parse(self, job: dict[str, Any]) -> JobPosting:
        return JobPosting(
            source=self.name,
            company=job.get("company_name", ""),
            title=job.get("title", ""),
            location=job.get("candidate_required_location", "Worldwide"),
            remote_type="full_remote",
            country="",
            employment_type="full_time",
            salary_min=self._parse_salary_min(job.get("salary") or ""),
            salary_max=self._parse_salary_max(job.get("salary") or ""),
            description_text=job.get("description", ""),
            application_url=job.get("url", ""),
            skills_mentioned=job.get("tags", []),
            raw_data=job,
        )

    def _parse_salary_min(self, salary_str: str) -> float | None:
        import re
        nums = re.findall(r"[\d,]+", salary_str.replace(",", ""))
        if nums:
            try:
                return float(nums[0])
            except ValueError:
                return None
        return None

    def _parse_salary_max(self, salary_str: str) -> float | None:
        import re
        nums = re.findall(r"[\d,]+", salary_str.replace(",", ""))
        if len(nums) >= 2:
            try:
                return float(nums[-1])
            except ValueError:
                return None
        return None
