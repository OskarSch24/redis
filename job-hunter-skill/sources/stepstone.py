"""StepStone adapter — German/European job board (DACH focus).

Hybrid adapter: uses Apify when a token + actor are configured, otherwise
falls back to direct HTML scraping of stepstone.de search pages.

Apify actor: memo23/stepstone-search-cheerio-ppr — one call per keyword
(single location per run), ~3 calls per run with the default query budget.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import date as dt_date
from typing import Any

from bs4 import BeautifulSoup

from schema import JobPosting
from sources.apify_client import ApifyClient
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.stepstone.de"
SEARCH_URL = "https://www.stepstone.de/jobs/{query}/in-{location}/"

APIFY_ACTOR = "memo23/stepstone-search-cheerio-ppr"


class StepStoneAdapter(BaseAdapter):
    name = "stepstone"
    rate_limit_delay = 3.0

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.apify = ApifyClient(config)

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        if self.apify.is_ready(self.name):
            return await self._fetch_via_apify(queries, locations)
        logger.info("[stepstone] Apify nicht konfiguriert — direkter Scrape-Fallback")
        return await self._fetch_direct(queries, locations)

    # ------------------------------------------------------------------ Apify

    async def _fetch_via_apify(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        search_terms = queries[:3] if queries else ["customer success manager"]
        # Actor takes a single location slug per run; StepStone is DACH,
        # default context is Germany.
        location = (locations[0] if locations else "deutschland").lower().replace(" ", "-")

        actor_id = self.apify.actor_id(self.name)
        postings: list[JobPosting] = []

        for query in search_terms:
            actor_input: dict[str, Any] = {
                "keyword": query,
                "location": location,
                "maxItems": self.apify.max_results,
                "includeRelatedJobs": False,
            }
            logger.info(
                "[stepstone] Apify: %r in %r via %s",
                query, location, actor_id,
            )
            items = await self.apify.run_sync(actor_id, actor_input)
            logger.info(
                "[stepstone] Apify returned %d items for %r/%r",
                len(items), query, location,
            )
            for item in items:
                try:
                    posting = self._parse_apify_item(item, location)
                    if posting:
                        postings.append(posting)
                except Exception as e:
                    logger.debug("[stepstone] Apify parse error: %s", e)

        seen: set[str] = set()
        unique: list[JobPosting] = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[stepstone] Fetched %d unique jobs via Apify", len(unique))
        return unique

    def _parse_apify_item(self, item: dict[str, Any], hint: str) -> JobPosting | None:
        url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("applicationUrl")
            or ""
        )
        if not url:
            return None
        if not url.startswith("http"):
            url = f"{BASE_URL}{url}"

        title = item.get("title") or item.get("jobTitle") or item.get("name") or ""

        company = (
            item.get("companyName")
            or item.get("company")
            or item.get("employer")
            or ""
        )
        if isinstance(company, dict):
            company = company.get("name") or company.get("title") or ""

        location = item.get("location") or item.get("city") or hint or ""
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        description = (
            item.get("textSnippet")
            or item.get("description")
            or item.get("jobDescription")
            or ""
        )

        salary_min = None
        salary_max = None
        salary_currency = None
        salary_period = None
        salary = item.get("unifiedSalary") or item.get("salaryRange")
        if not isinstance(salary, dict):
            salary = item.get("salary") if isinstance(item.get("salary"), dict) else None
        if isinstance(salary, dict):
            salary_min = salary.get("min") or salary.get("from")
            salary_max = salary.get("max") or salary.get("to")
            salary_currency = salary.get("currency")
            salary_period = salary.get("period")

        labels = item.get("labels") or item.get("topLabels") or []
        labels_text = " ".join(str(x) for x in labels) if isinstance(labels, list) else str(labels)
        emp_raw = (
            f"{item.get('employmentType') or item.get('contractType') or ''} {labels_text}"
        ).lower()
        if "part" in emp_raw or "teilzeit" in emp_raw:
            employment = "part_time"
        elif "freelance" in emp_raw or "freiberuflich" in emp_raw:
            employment = "freelance"
        elif "contract" in emp_raw or "befristet" in emp_raw:
            employment = "contract"
        else:
            employment = "full_time"

        wfh = item.get("workFromHome") or item.get("remoteType") or ""
        if isinstance(wfh, bool):
            remote_signal = "remote" if wfh else ""
        else:
            wfh_l = str(wfh).lower()
            if "hybrid" in wfh_l or "partial" in wfh_l:
                remote_signal = "hybrid"
            elif "remote" in wfh_l or "home" in wfh_l:
                remote_signal = "remote"
            else:
                remote_signal = ""

        posted = None
        for key in ("datePosted", "publishFromDate", "postedDate", "publishedAt", "createdAt"):
            val = item.get(key)
            if isinstance(val, str) and val:
                try:
                    posted = dt_date.fromisoformat(val[:10])
                    break
                except (ValueError, TypeError):
                    continue

        raw: dict[str, Any] = {"apify": True, "actor": APIFY_ACTOR}
        for k in ("workFromHome", "labels", "skills", "postCode",
                  "isSponsored", "isTopJob", "companyUrl"):
            if k in item:
                raw[k] = item[k]

        posting = JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(
                f"{remote_signal} {title} {location} {description[:500]}"
            ),
            country="DE",
            employment_type=employment,
            description_text=description[:5000] if description else "",
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            posted_date=posted,
            application_url=url,
            raw_data=raw,
        )
        if salary_currency:
            posting.salary_currency = str(salary_currency)
        if salary_period:
            posting.salary_period = str(salary_period)
        return posting

    # ----------------------------------------------------------- Direct scrape

    async def _fetch_direct(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        search_terms = queries[:3] if queries else ["customer success manager"]
        search_locations = locations[:2] if locations else ["deutschland"]

        for query in search_terms:
            for location in search_locations:
                encoded_q = urllib.parse.quote(query.replace(" ", "-"))
                encoded_l = urllib.parse.quote(location.lower().replace(" ", "-"))
                url = f"{BASE_URL}/jobs/{encoded_q}/in-{encoded_l}/"

                page = 1
                while page <= 5:
                    page_url = url if page == 1 else f"{url}?page={page}"
                    resp = await self._get(page_url)
                    if not resp:
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(
                        "article[data-at='job-item'], "
                        ".res-1r46nm7, "
                        "[class*='ResultItem']"
                    )

                    if not cards:
                        break

                    for card in cards:
                        try:
                            posting = self._parse_card(card)
                            if posting:
                                postings.append(posting)
                        except Exception as e:
                            logger.debug("[stepstone] Parse error: %s", e)

                    page += 1
                    await self._sleep()

        seen = set()
        unique = []
        for p in postings:
            if p.application_url not in seen:
                seen.add(p.application_url)
                unique.append(p)

        logger.info("[stepstone] Fetched %d unique jobs", len(unique))
        return unique

    def _parse_card(self, card: Any) -> JobPosting | None:
        link = card.select_one("a[data-at='job-item-title'], a[href*='/stellenangebote--']")
        if not link:
            link = card.select_one("h2 a, h3 a")
        if not link:
            return None

        href = link.get("href", "")
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        title = link.get_text(strip=True)

        company_el = card.select_one(
            "[data-at='job-item-company-name'], "
            ".res-nehv70, "
            "[class*='CompanyName']"
        )
        company = company_el.get_text(strip=True) if company_el else ""

        location_el = card.select_one(
            "[data-at='job-item-location'], "
            "[class*='Location']"
        )
        location = location_el.get_text(strip=True) if location_el else "Deutschland"

        return JobPosting(
            source=self.name,
            company=company,
            title=title,
            location=location,
            remote_type=self._normalize_remote_type(f"{title} {location}"),
            country="DE",
            employment_type="full_time",
            application_url=url,
            raw_data={"href": href},
        )
