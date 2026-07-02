"""WeWorkRemotely adapter — uses RSS feeds."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

from schema import JobPosting
from sources.base import BaseAdapter

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-sales-jobs.rss",
    "https://weworkremotely.com/categories/remote-business-exec-management-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
]


class WeWorkRemotelyAdapter(BaseAdapter):
    name = "weworkremotely"
    rate_limit_delay = 2.0

    async def fetch_all(self, queries: list[str], locations: list[str]) -> list[JobPosting]:
        postings: list[JobPosting] = []
        seen_urls: set[str] = set()

        for feed_url in RSS_FEEDS:
            resp = await self._get(feed_url)
            if not resp:
                continue
            try:
                items = self._parse_rss(resp.text, queries)
                for p in items:
                    if p.application_url not in seen_urls:
                        seen_urls.add(p.application_url)
                        postings.append(p)
            except Exception as e:
                logger.warning("[weworkremotely] RSS parse error: %s", e)
            await self._sleep()

        logger.info("[weworkremotely] Fetched %d jobs", len(postings))
        return postings

    def _parse_rss(self, xml_text: str, queries: list[str]) -> list[JobPosting]:
        root = ET.fromstring(xml_text)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        postings = []

        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            pub_el = item.find("pubDate")

            title = (title_el.text or "") if title_el is not None else ""
            # WWR titles are like "Company: Job Title"
            if ": " in title:
                company, title = title.split(": ", 1)
            else:
                company = ""

            if not self._title_relevant(title, queries):
                continue

            link = (link_el.text or "") if link_el is not None else ""
            desc = (desc_el.text or "") if desc_el is not None else ""

            posted = None
            if pub_el is not None and pub_el.text:
                try:
                    posted = datetime.strptime(pub_el.text[:25], "%a, %d %b %Y %H:%M:%S").date()
                except ValueError:
                    pass

            postings.append(JobPosting(
                source=self.name,
                company=company.strip(),
                title=title.strip(),
                location="Worldwide",
                remote_type="full_remote",
                country="",
                employment_type="full_time",
                description_text=desc,
                application_url=link,
                posted_date=posted,
                raw_data={"title": title, "link": link},
            ))
        return postings

    def _title_relevant(self, title: str, queries: list[str]) -> bool:
        if not queries:
            return True
        lower = title.lower()
        return any(q.lower() in lower for q in queries)
