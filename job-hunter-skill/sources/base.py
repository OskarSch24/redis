"""Base adapter — all source adapters inherit from this."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from schema import JobPosting

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


class BaseAdapter(ABC):
    name: str = "base"
    rate_limit_delay: float = 1.5
    max_retries: int = 3
    timeout: int = 30

    def __init__(self, config: dict[str, Any]):
        self.config = config
        rl = config.get("rate_limiting", {})
        # Config liefert das globale Minimum; pro Adapter getunte Delays bleiben erhalten
        self.rate_limit_delay = max(
            float(rl.get("default_delay_seconds", 1.5)), type(self).rate_limit_delay
        )
        self.max_retries = int(rl.get("max_retries", self.max_retries))
        self.timeout = int(rl.get("timeout_seconds", self.timeout))
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": self._random_ua()},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _random_ua(self) -> str:
        return random.choice(_USER_AGENTS)

    async def _get(self, url: str, **kwargs) -> httpx.Response | None:
        for attempt in range(1, self.max_retries + 1):
            try:
                assert self._client is not None
                resp = await self._client.get(
                    url,
                    headers={"User-Agent": self._random_ua()},
                    **kwargs,
                )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = self.rate_limit_delay * (2 ** attempt)
                    logger.warning("[%s] Rate limited — waiting %.1fs", self.name, wait)
                    await asyncio.sleep(wait)
                elif e.response.status_code in (403, 404):
                    logger.debug("[%s] %s → %s", self.name, url, e.response.status_code)
                    return None
                else:
                    logger.warning("[%s] HTTP %s for %s (attempt %d/%d)",
                                   self.name, e.response.status_code, url, attempt, self.max_retries)
            except httpx.TransportError as e:
                logger.warning("[%s] Network error for %s: %s (attempt %d/%d)",
                               self.name, url, e, attempt, self.max_retries)
            if attempt < self.max_retries:
                await asyncio.sleep(self.rate_limit_delay * attempt)
        return None

    async def _sleep(self):
        jitter = random.uniform(0, self.rate_limit_delay * 0.3)
        await asyncio.sleep(self.rate_limit_delay + jitter)

    @abstractmethod
    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        """Fetch all jobs for the given queries and locations."""
        ...

    def _normalize_remote_type(self, text: str) -> str:
        lower = text.lower()
        if any(k in lower for k in ["fully remote", "100% remote", "remote first", "remote-first"]):
            return "full_remote"
        if "hybrid" in lower:
            return "hybrid"
        if "remote" in lower:
            return "full_remote"
        return "unknown"
