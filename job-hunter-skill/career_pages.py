"""Career pages crawler — fetches jobs from ATS platforms directly."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

import httpx

from schema import JobPosting

logger = logging.getLogger(__name__)

PLATFORM_APIS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v3/accounts/{slug}/jobs",
    "personio": "https://{slug}.jobs.personio.com/xml",
    "bamboohr": "https://{slug}.bamboohr.com/jobs/embed2.php?version=1.0.0",
    "recruitee": "https://{slug}.recruitee.com/api/offers/",
    "teamtailor": "https://api.teamtailor.com/v1/jobs",
}


class CareerPagesCrawler:
    def __init__(self, companies: list[dict[str, Any]], config: dict[str, Any]):
        self.companies = companies
        self.config = config
        rl = config.get("rate_limiting", {})
        self.timeout = int(rl.get("timeout_seconds", 30))
        self.delay = float(rl.get("default_delay_seconds", 1.5))

    async def fetch_all(self, queries: list[str]) -> list[JobPosting]:
        import asyncio
        postings: list[JobPosting] = []

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            tasks = [
                self._fetch_company(client, company, queries)
                for company in self.companies
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Career page fetch error: %s", result)
                elif isinstance(result, list):
                    postings.extend(result)

        logger.info("[career_pages] Fetched %d jobs from %d companies",
                    len(postings), len(self.companies))
        return postings

    async def _fetch_company(
        self, client: httpx.AsyncClient, company: dict[str, Any], queries: list[str]
    ) -> list[JobPosting]:
        import asyncio
        platform = company.get("platform", "")
        slug = company.get("slug", "")
        name = company.get("name", slug)
        country = company.get("country", "")

        if not platform or not slug:
            return []

        try:
            handler = getattr(self, f"_fetch_{platform}", self._fetch_unknown)
            jobs = await handler(client, slug, name, country)
            await asyncio.sleep(self.delay)

            filtered = [j for j in jobs if self._is_relevant(j, queries)]
            if filtered:
                logger.info("[career_pages] %s: %d relevant jobs", name, len(filtered))
            return filtered

        except Exception as e:
            logger.debug("[career_pages] %s (%s) error: %s", name, platform, e)
            return []

    def _is_relevant(self, job: JobPosting, queries: list[str]) -> bool:
        if not queries:
            return True
        combined = f"{job.title} {job.description_text}".lower()
        return any(q.lower() in combined for q in queries)

    async def _fetch_greenhouse(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["greenhouse"].format(slug=slug)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        jobs = data.get("jobs", [])
        return [self._parse_greenhouse(j, name, country) for j in jobs]

    def _parse_greenhouse(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        location = job.get("location", {}).get("name", "") if isinstance(job.get("location"), dict) else ""
        return JobPosting(
            source="greenhouse",
            company=company,
            title=job.get("title", ""),
            location=location,
            remote_type="unknown",
            country=country,
            employment_type="full_time",
            description_text=job.get("content", ""),
            application_url=job.get("absolute_url", ""),
            raw_data=job,
        )

    async def _fetch_lever(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["lever"].format(slug=slug)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        jobs = resp.json()
        if not isinstance(jobs, list):
            jobs = jobs.get("data", [])
        return [self._parse_lever(j, name, country) for j in jobs]

    def _parse_lever(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        categories = job.get("categories", {})
        location = categories.get("location", "") if isinstance(categories, dict) else ""
        commitment = categories.get("commitment", "full_time") if isinstance(categories, dict) else "full_time"
        return JobPosting(
            source="lever",
            company=company,
            title=job.get("text", ""),
            location=location,
            remote_type=self._normalize_remote_type(location),
            country=country,
            employment_type=self._map_commitment(commitment),
            description_text=job.get("descriptionPlain", ""),
            application_url=job.get("hostedUrl", ""),
            raw_data=job,
        )

    def _map_commitment(self, commitment: str) -> str:
        lower = commitment.lower()
        if "part" in lower:
            return "part_time"
        if "contract" in lower or "freelance" in lower:
            return "contract"
        return "full_time"

    async def _fetch_ashby(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["ashby"].format(slug=slug)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        data = resp.json()
        jobs = data.get("jobs", [])
        return [self._parse_ashby(j, name, country) for j in jobs]

    def _parse_ashby(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        location = job.get("location", "")
        if isinstance(location, dict):
            location = location.get("locationStr", "")
        return JobPosting(
            source="ashby",
            company=company,
            title=job.get("title", ""),
            location=location,
            remote_type=self._normalize_remote_type(location),
            country=country,
            employment_type="full_time",
            description_text=job.get("descriptionHtml", ""),
            description_html=job.get("descriptionHtml", ""),
            application_url=job.get("jobUrl", ""),
            raw_data=job,
        )

    async def _fetch_workable(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["workable"].format(slug=slug)
        resp = await client.post(url, json={"query": "", "limit": 100})
        if resp.status_code not in (200, 201):
            resp = await client.get(url.replace("/jobs", ""))
            if resp.status_code != 200:
                return []
        try:
            data = resp.json()
            jobs = data.get("results", [])
        except Exception:
            return []
        return [self._parse_workable(j, name, country) for j in jobs]

    def _parse_workable(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        location = job.get("location", {})
        loc_str = location.get("country", "") if isinstance(location, dict) else str(location)
        return JobPosting(
            source="workable",
            company=company,
            title=job.get("title", ""),
            location=loc_str,
            remote_type=self._normalize_remote_type(
                "remote" if job.get("remote") else loc_str
            ),
            country=country,
            employment_type=self._map_employment(job.get("employment_type", "")),
            description_text=job.get("description", ""),
            application_url=job.get("url", job.get("shortlink", "")),
            raw_data=job,
        )

    def _map_employment(self, etype: str) -> str:
        lower = etype.lower()
        if "part" in lower:
            return "part_time"
        if "contract" in lower or "temporary" in lower:
            return "contract"
        if "freelance" in lower:
            return "freelance"
        return "full_time"

    async def _fetch_personio(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["personio"].format(slug=slug)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        try:
            root = ET.fromstring(resp.text)
            return [self._parse_personio(job, name, country) for job in root.findall(".//position")]
        except ET.ParseError:
            return []

    def _parse_personio(self, job: Any, company: str, country: str) -> JobPosting:
        def _text(tag: str) -> str:
            el = job.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        return JobPosting(
            source="personio",
            company=company,
            title=_text("name"),
            location=_text("office"),
            remote_type=self._normalize_remote_type(_text("office")),
            country=country,
            employment_type=self._map_employment(_text("employment_type")),
            description_text=_text("job_description"),
            application_url=_text("job_positions_url"),
            raw_data={},
        )

    async def _fetch_recruitee(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        url = PLATFORM_APIS["recruitee"].format(slug=slug)
        resp = await client.get(url)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
            jobs = data.get("offers", [])
        except Exception:
            return []
        return [self._parse_recruitee(j, name, country) for j in jobs]

    def _parse_recruitee(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        return JobPosting(
            source="recruitee",
            company=company,
            title=job.get("title", ""),
            location=job.get("city", ""),
            remote_type=self._normalize_remote_type(job.get("remote", "")),
            country=country,
            employment_type=self._map_employment(job.get("employment_type_code", "")),
            description_text=job.get("description", ""),
            application_url=job.get("careers_url", ""),
            raw_data=job,
        )

    async def _fetch_teamtailor(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        headers = {"X-Api-Version": "20210218"}
        url = f"https://api.teamtailor.com/v1/jobs?filter[department]={slug}"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
            jobs = data.get("data", [])
        except Exception:
            return []
        return [self._parse_teamtailor(j, name, country) for j in jobs]

    def _parse_teamtailor(self, job: dict[str, Any], company: str, country: str) -> JobPosting:
        attrs = job.get("attributes", {})
        return JobPosting(
            source="teamtailor",
            company=company,
            title=attrs.get("title", ""),
            location=attrs.get("remote-status", ""),
            remote_type=self._normalize_remote_type(attrs.get("remote-status", "")),
            country=country,
            employment_type="full_time",
            description_text=attrs.get("body", ""),
            application_url=attrs.get("career-site-url", ""),
            raw_data=job,
        )

    async def _fetch_unknown(
        self, client: httpx.AsyncClient, slug: str, name: str, country: str
    ) -> list[JobPosting]:
        logger.debug("[career_pages] Unknown platform for %s", name)
        return []

    def _normalize_remote_type(self, text: str) -> str:
        lower = str(text).lower()
        if any(k in lower for k in ["fully remote", "100% remote", "remote first"]):
            return "full_remote"
        if "hybrid" in lower:
            return "hybrid"
        if "remote" in lower:
            return "full_remote"
        return "unknown"
