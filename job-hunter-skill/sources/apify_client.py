"""Shared Apify client — runs actors synchronously and returns dataset items.

Used by LinkedIn, Glassdoor, and Indeed adapters when a token is configured.
Falls back to direct scraping when no token is available.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

APIFY_API_BASE = "https://api.apify.com/v2"


class ApifyClient:
    """Minimal Apify REST client. Token resolution order: APIFY_API_TOKEN env > config."""

    def __init__(self, config: dict[str, Any]):
        cfg = config.get("apify", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.token = os.environ.get("APIFY_API_TOKEN") or cfg.get("api_token", "") or ""
        self.timeout = int(cfg.get("timeout_seconds", 600))
        self.actors = cfg.get("actors", {}) or {}
        self.max_results = int(cfg.get("max_results_per_search", 100))
        self._warned_no_token = False

    def is_ready(self, source: str) -> bool:
        if not self.enabled:
            return False
        if not self.token:
            if not self._warned_no_token:
                logger.warning(
                    "Apify enabled in config but no token — set APIFY_API_TOKEN env var "
                    "or apify.api_token in config.yaml. Falling back to direct scraping."
                )
                self._warned_no_token = True
            return False
        if not self.actors.get(source):
            logger.debug("[apify:%s] No actor configured", source)
            return False
        return True

    def actor_id(self, source: str) -> str | None:
        return self.actors.get(source)

    async def run_sync(self, actor_id: str, actor_input: dict[str, Any]) -> list[dict[str, Any]]:
        """Run an actor and return dataset items. Endpoint: run-sync-get-dataset-items."""
        actor_path = actor_id.replace("/", "~")
        url = f"{APIFY_API_BASE}/acts/{actor_path}/run-sync-get-dataset-items"
        params = {"token": self.token, "timeout": str(self.timeout)}

        async with httpx.AsyncClient(timeout=self.timeout + 30) as client:
            try:
                resp = await client.post(url, params=params, json=actor_input)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    return data
                logger.warning("[apify:%s] Unexpected response shape: %s", actor_id, type(data).__name__)
                return []
            except httpx.HTTPStatusError as e:
                body = (e.response.text[:300] if e.response is not None else "")
                logger.error("[apify:%s] HTTP %s: %s", actor_id, e.response.status_code, body)
                return []
            except httpx.TimeoutException:
                logger.error("[apify:%s] Timeout after %ds", actor_id, self.timeout + 30)
                return []
            except Exception as e:
                logger.error("[apify:%s] Unexpected error: %s", actor_id, e)
                return []
