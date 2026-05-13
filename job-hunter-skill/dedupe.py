"""Multi-stage deduplication of job postings."""

from __future__ import annotations

import logging
from typing import Any

from schema import JobPosting

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz not installed — fuzzy deduplication disabled. Run: pip install rapidfuzz")


def deduplicate(postings: list[JobPosting], fuzzy_threshold: int = 90) -> list[JobPosting]:
    """
    Multi-stage deduplication:
    1. Exact match on application_url
    2. Fuzzy match on (company + title) with configurable threshold
    Returns deduplicated list, preferring richest records.
    """
    if not postings:
        return []

    # Stage 1: Exact URL dedup — keep richest record per URL
    by_url: dict[str, JobPosting] = {}
    no_url: list[JobPosting] = []

    for p in postings:
        url = _normalize_url(p.application_url)
        if not url:
            no_url.append(p)
            continue
        if url not in by_url or p.richness_score() > by_url[url].richness_score():
            by_url[url] = p

    url_deduped = list(by_url.values())
    total_url_dups = len(postings) - len(url_deduped) - len(no_url)
    logger.info("URL dedup: removed %d exact duplicates", total_url_dups)

    # Stage 2: Fuzzy dedup on company+title (only if rapidfuzz available)
    all_candidates = url_deduped + no_url

    if not RAPIDFUZZ_AVAILABLE or fuzzy_threshold >= 100:
        logger.info("Skipping fuzzy dedup — %d postings remaining", len(all_candidates))
        return all_candidates

    result: list[JobPosting] = []
    fuzzy_dups = 0

    for candidate in all_candidates:
        key = _make_fuzzy_key(candidate)
        is_dup = False

        for existing in result:
            existing_key = _make_fuzzy_key(existing)
            score = fuzz.token_sort_ratio(key, existing_key)
            if score >= fuzzy_threshold:
                # Keep the richer one
                if candidate.richness_score() > existing.richness_score():
                    result.remove(existing)
                    result.append(candidate)
                is_dup = True
                fuzzy_dups += 1
                break

        if not is_dup:
            result.append(candidate)

    logger.info("Fuzzy dedup: removed %d near-duplicates — %d unique jobs remaining",
                fuzzy_dups, len(result))
    return result


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().lower().rstrip("/")
    # Remove tracking parameters
    if "?" in url:
        base, params = url.split("?", 1)
        keep = []
        for param in params.split("&"):
            if "=" in param:
                key, _ = param.split("=", 1)
                if key not in {"utm_source", "utm_medium", "utm_campaign", "ref", "source", "tracking"}:
                    keep.append(param)
        url = base + ("?" + "&".join(keep) if keep else "")
    return url


def _make_fuzzy_key(posting: JobPosting) -> str:
    company = posting.company.lower().strip()
    title = posting.title.lower().strip()
    # Normalize common abbreviations
    title = title.replace("manager", "mgr").replace("senior", "sr").replace("junior", "jr")
    return f"{company} {title}"
