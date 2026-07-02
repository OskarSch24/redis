"""Jobgether adapter — remote/flexible-jobs platform.

Apify-first adapter using the generic apify/web-scraper actor with a custom
pageFunction (jobgether.com is a React SPA, so plain HTML fetches miss the
job cards). Falls back to the original direct HTML scrape when no Apify
token/actor is configured.

Because we define the pageFunction ourselves, the dataset items have our
own shape: {url, title, company, location, description}.
"""

from __future__ import annotations

import logging
from datetime import date as dt_date
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://jobgether.com"
SEARCH_URL = "https://jobgether.com/offer"

# Runs inside the browser (apify/web-scraper page function). Waits until the
# SPA has rendered job cards, then returns one {url,title,company,location,
# description} object per card. Returning an array stores each element as a
# separate dataset item.
PAGE_FUNCTION = r"""async function pageFunction(context) {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    // jobgether.com is a React SPA — poll until job-offer links are rendered.
    let anchors = [];
    for (let i = 0; i < 30; i++) {
        anchors = Array.from(document.querySelectorAll('a[href]')).filter((a) => {
            const href = a.getAttribute('href') || '';
            return /\/offers?\//.test(href);
        });
        if (anchors.length > 0) break;
        await sleep(1000);
    }

    const results = [];
    const seen = new Set();
    for (const a of anchors) {
        const url = a.href || '';
        if (!url || seen.has(url)) continue;
        seen.add(url);

        const card = a.closest(
            "article, li, [class*='card'], [class*='offer'], [class*='job']"
        ) || a;
        const pick = (sel) => {
            const el = card.querySelector(sel);
            return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
        };

        const title = pick("h2, h3, [class*='title']")
            || a.textContent.replace(/\s+/g, ' ').trim();
        if (!title) continue;

        results.push({
            url: url,
            title: title,
            company: pick("[class*='company'], [class*='employer'], [class*='organization']"),
            location: pick("[class*='location'], [class*='place'], [class*='city']"),
            description: (card.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 2000),
        });
    }
    return results;
}
"""


class JobgetherAdapter(BaseAdapter):
    name = "jobgether"
    rate_limit_delay = 2.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if self.apify.is_ready(self.name):
            return await self._fetch_via_apify(queries, locations)
        logger.info("[jobgether] Apify nicht konfiguriert — direkter Scrape-Fallback")
        return await self._fetch_direct(queries, locations)

    # ------------------------------------------------------------------ Apify

    async def _fetch_via_apify(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        search_terms = queries[:3] if queries else ["customer success"]
        start_urls = [
            {"url": f"{SEARCH_URL}?search={quote_plus(query)}"}
            for query in search_terms
        ]

        actor_id = self.apify.actor_id(self.name)
        actor_input: dict[str, Any] = {
            "startUrls": start_urls,
            "pageFunction": PAGE_FUNCTION,
            "proxyConfiguration": {"useApifyProxy": True},
            "maxPagesPerCrawl": 15,
            "waitUntil": ["networkidle2"],
        }

        logger.info(
            "[jobgether] Apify: %d start URLs via %s",
            len(start_urls), actor_id,
        )
        items = await self.apify.run_sync(actor_id, actor_input)
        logger.info("[jobgether] Apify returned %d items", len(items))

        postings: list[JobPosting] = []
        for item in items[: self.apify.max_results]:
            try:
                posting = self._parse_apify_item(item, "")
                if posting:
                    postings.append(posting)
            except Exception as e:
                logger.debug("[jobgether] Apify parse error: %s", e)

        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[jobgether] Fetched %d unique jobs via Apify", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], hint: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("link")
            or item.get("href")
            or item.get("jobUrl")
            or ""
        )
        if not url:
            return None
        if not url.startswith("http"):
            url = f"{BASE_URL}{url}"

        title = (
            item.get("title")
            or item.get("jobTitle")
            or item.get("name")
            or item.get("position")
            or ""
        )

        company = item.get("company") or item.get("companyName") or item.get("employer") or ""
        if isinstance(company, dict):
            company = company.get("name") or company.get("title") or ""

        location = item.get("location") or item.get("city") or item.get("place") or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        description = (
            item.get("description")
            or item.get("descriptionText")
            or item.get("text")
            or ""
        )

        emp_raw = (item.get("employmentType") or item.get("contractType") or "").lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "freelance" in emp_raw:
            employment = "freelance"
        elif "contract" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        posted = None
        for key in ("postedDate", "publishedAt", "datePosted", "createdAt"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    continue

        raw: dict[str, Any] = {"apify": True, "actor": "apify/web-scraper"}
        if hint:
            raw["hint"] = hint
        for k in ("contractType", "employmentType", "salary", "tags"):
            if k in item:
                raw[k] = item[k]

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(
                f"{title} {location} {description[:500]}"
            ),
            country="",
            employment_type=employment,
            description_text=description[:5000] if description else "",
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )

    # ----------------------------------------------------- Direct scrape (Fallback)

    async def _fetch_direct(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success"]

        for query in search_terms:
            page = 1
            while page <= 5:
                resp = await self._get(
                    SEARCH_URL,
                    params={"search": query, "page": page},
                )
                if not resp:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select("article, .job-card, [class*='offer']")
                if not cards:
                    break

                for card in cards:
                    try:
                        posting = self._parse_card(card)
                        if posting:
                            postings.append(posting)
                    except Exception as e:
                        logger.debug("[jobgether] Parse error: %s", e)

                page += 1
                await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[jobgether] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any) -> JobPosting | None:
        link = card.select_one("a[href*='/offer/']")
        if not link:
            return None
        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"

        title_el = card.select_one("h2, h3, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        company_el = card.select_one("[class*='company'], [class*='employer']")
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one("[class*='location'], [class*='place']")
        location = location_el.get_text(strip=True) if location_el else "Remote"

        remote = self._normalize_remote_type(location)

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=remote,
            country="",
            employment_type="full_time",
            application_url=url,
            raw_data={"href": href},
        )
