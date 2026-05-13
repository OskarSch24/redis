"""Verifier — checks if top-scored jobs are still active."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from schema import JobPosting

logger = logging.getLogger(__name__)


class Verifier:
    def __init__(self, config: dict[str, Any]):
        rl = config.get("rate_limiting", {})
        self.timeout = int(rl.get("timeout_seconds", 20))
        self.delay = float(rl.get("default_delay_seconds", 1.0))
        self.max_concurrent = 10

    async def verify_all(self, postings: list[JobPosting]) -> list[JobPosting]:
        if not postings:
            return postings

        logger.info("[verify] Verifying %d jobs...", len(postings))
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-hunter-skill/1.0)"},
        ) as client:
            tasks = [
                self._verify_one(client, semaphore, posting)
                for posting in postings
            ]
            verified = await asyncio.gather(*tasks, return_exceptions=True)

        result: list[JobPosting] = []
        for i, v in enumerate(verified):
            if isinstance(v, Exception):
                posting = postings[i]
                posting.is_verified = True
                posting.is_active = None
                result.append(posting)
            else:
                result.append(v)

        active = sum(1 for p in result if p.is_active is True)
        inactive = sum(1 for p in result if p.is_active is False)
        logger.info("[verify] Active: %d | Inactive: %d | Unknown: %d",
                    active, inactive, len(result) - active - inactive)
        return result

    async def _verify_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        posting: JobPosting,
    ) -> JobPosting:
        async with semaphore:
            await asyncio.sleep(self.delay * 0.5)

            if not posting.application_url:
                posting.is_verified = True
                posting.is_active = None
                return posting

            try:
                resp = await client.head(posting.application_url)
                status = resp.status_code

                if status == 405:
                    # HEAD not allowed — try GET
                    resp = await client.get(posting.application_url)
                    status = resp.status_code

                posting.is_active = status in (200, 201, 301, 302)

                if posting.is_active and status == 200 and resp.headers.get("content-type", "").startswith("text/html"):
                    content = resp.text.lower()
                    if any(phrase in content for phrase in [
                        "job no longer available",
                        "this position has been filled",
                        "job has been closed",
                        "stelle nicht mehr verfügbar",
                        "position is closed",
                        "page not found",
                        "404",
                    ]):
                        posting.is_active = False

            except (httpx.ConnectError, httpx.TimeoutException, httpx.TooManyRedirects):
                posting.is_active = None

            posting.is_verified = True
            return posting
