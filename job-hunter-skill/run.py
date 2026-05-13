#!/usr/bin/env python3
"""
Job-Hunter — Hauptorchestrator
Aggregiert, dedupliziert, scored und exportiert Stellenangebote.

Usage:
    python run.py [--config config.yaml] [--max-jobs 5000] [--output output/jobs.xlsx]
                  [--resume] [--sources SOURCE,...] [--top 100] [--no-verify]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).parent


def setup_logging(config: dict[str, Any], timestamp: str) -> logging.Logger:
    log_dir = SKILL_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    log_cfg = config.get("logging", {})
    level_name = log_cfg.get("level", "INFO")
    level = getattr(logging, level_name, logging.INFO)

    log_file = log_dir / f"run_{timestamp}.log"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("job-hunter")


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_companies(companies_path: Path) -> list[dict[str, Any]]:
    if not companies_path.exists():
        return []
    with open(companies_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("companies", [])


def get_queries(config: dict[str, Any]) -> list[str]:
    queries = config.get("search_queries", {})
    default = queries.get("default", [])
    # Merge all query sets and deduplicate
    all_queries: list[str] = []
    seen: set[str] = set()
    for q in default:
        lower = q.lower()
        if lower not in seen:
            seen.add(lower)
            all_queries.append(q)
    return all_queries


def get_locations(config: dict[str, Any]) -> list[str]:
    loc_cfg = config.get("location", {})
    countries = loc_cfg.get("countries", [])
    cities = loc_cfg.get("cities", [])
    locations: list[str] = cities.copy()
    country_names = {
        "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
        "MT": "Malta", "NL": "Netherlands", "UK": "United Kingdom",
    }
    for country in countries:
        name = country_names.get(country.upper(), country)
        if name not in locations:
            locations.append(name)
    return locations


def save_raw_data(postings: list[Any], source: str, timestamp: str):
    raw_dir = SKILL_DIR / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{source}_{timestamp}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                [p.model_dump(mode="json") for p in postings],
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
    except Exception as e:
        logging.getLogger("job-hunter").warning("Failed to save raw data for %s: %s", source, e)


def load_cached_raw(timestamp: str | None = None) -> list[Any]:
    """Load previously saved raw data for --resume mode."""
    from schema import JobPosting

    raw_dir = SKILL_DIR / "data" / "raw"
    if not raw_dir.exists():
        return []

    pattern = f"*_{timestamp}.json" if timestamp else "*.json"
    postings: list[JobPosting] = []

    for json_file in sorted(raw_dir.glob(pattern)):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                try:
                    postings.append(JobPosting.model_validate(item))
                except Exception:
                    pass
        except Exception as e:
            logging.getLogger("job-hunter").warning("Failed to load %s: %s", json_file, e)

    return postings


def build_adapters(enabled_sources: list[str], config: dict[str, Any]) -> list[Any]:
    """Import and instantiate all enabled source adapters."""
    sys.path.insert(0, str(SKILL_DIR))

    adapter_map = {
        "arbeitnow": ("sources.arbeitnow", "ArbeitnowAdapter"),
        "remoteok": ("sources.remoteok", "RemoteOKAdapter"),
        "remotive": ("sources.remotive", "RemotiveAdapter"),
        "weworkremotely": ("sources.weworkremotely", "WeWorkRemotelyAdapter"),
        "jobicy": ("sources.jobicy", "JobicyAdapter"),
        "workingnomads": ("sources.workingnomads", "WorkingNomadsAdapter"),
        "4dayweek": ("sources.fourdayweek", "FourDayWeekAdapter"),
        "jobgether": ("sources.jobgether", "JobgetherAdapter"),
        "germantechjobs": ("sources.germantechjobs", "GermanTechJobsAdapter"),
        "berlinstartupjobs": ("sources.berlinstartupjobs", "BerlinStartupJobsAdapter"),
        "relocateme": ("sources.relocateme", "RelocateMeAdapter"),
        "justremote": ("sources.justremote", "JustRemoteAdapter"),
        "workwide": ("sources.workwide", "WorkwideAdapter"),
        "meetfrank": ("sources.meetfrank", "MeetFrankAdapter"),
        "jobsinmalta": ("sources.jobsinmalta", "JobsInMaltaAdapter"),
        "konnekt": ("sources.konnekt", "KonnektAdapter"),
        "stepstone": ("sources.stepstone", "StepStoneAdapter"),
        "indeed": ("sources.indeed", "IndeedAdapter"),
        "linkedin": ("sources.linkedin", "LinkedInAdapter"),
        "glassdoor": ("sources.glassdoor", "GlassdoorAdapter"),
        "jobsforgermans": ("sources.jobsforgermans", "JobsForGermansAdapter"),
    }

    adapters = []
    logger = logging.getLogger("job-hunter")

    for source_name in enabled_sources:
        if source_name not in adapter_map:
            logger.warning("Unknown source: %s — skipping", source_name)
            continue
        module_path, class_name = adapter_map[source_name]
        try:
            import importlib
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            adapters.append(cls(config))
        except ImportError as e:
            logger.warning("Could not load adapter %s: %s", source_name, e)

    return adapters


async def fetch_from_adapter(
    adapter: Any,
    queries: list[str],
    locations: list[str],
    timestamp: str,
    logger: logging.Logger,
) -> list[Any]:
    try:
        async with adapter:
            postings = await adapter.fetch_all(queries, locations)
        save_raw_data(postings, adapter.name, timestamp)
        return postings
    except Exception as e:
        logger.error("[%s] Fatal error: %s", adapter.name, e, exc_info=True)
        return []


async def run(args: argparse.Namespace):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_path = Path(args.config) if args.config else SKILL_DIR / "config.yaml"

    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}")
        print("Please copy and customize config.yaml first.")
        sys.exit(1)

    config = load_config(config_path)
    logger = setup_logging(config, timestamp)

    logger.info("=" * 60)
    logger.info("Job-Hunter gestartet — %s", timestamp)
    logger.info("Config: %s", config_path)
    logger.info("=" * 60)

    queries = get_queries(config)
    locations = get_locations(config)
    logger.info("Suchanfragen: %s", queries)
    logger.info("Standorte: %s", locations)

    output_cfg = config.get("output", {})
    max_jobs = args.max_jobs or output_cfg.get("max_jobs", 5000)
    top_verify = args.top or output_cfg.get("top_verify", 100)

    output_filename = output_cfg.get("excel_filename", "jobs_{timestamp}.xlsx").replace(
        "{timestamp}", timestamp
    )
    output_path = SKILL_DIR / "output" / output_filename
    if args.output:
        output_path = Path(args.output)

    # --- Import core modules ---
    sys.path.insert(0, str(SKILL_DIR))
    from dedupe import deduplicate
    from scorer import load_scorer
    from verify import Verifier
    from export import export_to_excel

    all_postings: list[Any] = []

    # --- Resume mode ---
    if args.resume:
        logger.info("Resume-Modus: Lade gecachte Rohdaten...")
        all_postings = load_cached_raw()
        logger.info("Geladen: %d Jobs aus Cache", len(all_postings))
    else:
        # --- Determine enabled sources ---
        if args.sources:
            enabled = [s.strip() for s in args.sources.split(",")]
        else:
            enabled = list({
                "arbeitnow", "remoteok", "remotive", "weworkremotely", "jobicy",
                "workingnomads", "4dayweek", "jobgether", "germantechjobs",
                "berlinstartupjobs", "relocateme", "justremote", "workwide",
                "meetfrank", "jobsinmalta", "konnekt", "jobsforgermans",
                "stepstone", "indeed", "linkedin", "glassdoor",
            })

        adapters = build_adapters(enabled, config)
        logger.info("Aktive Quellen (%d): %s", len(adapters), [a.name for a in adapters])

        # --- Fetch from all source adapters in parallel (with concurrency limit) ---
        semaphore = asyncio.Semaphore(5)

        async def limited_fetch(adapter):
            async with semaphore:
                return await fetch_from_adapter(adapter, queries, locations, timestamp, logger)

        logger.info("Starte paralleles Fetching...")
        t0 = time.monotonic()

        tasks = [limited_fetch(adapter) for adapter in adapters]
        results = await asyncio.gather(*tasks)

        for source_postings in results:
            all_postings.extend(source_postings)

        elapsed = time.monotonic() - t0
        logger.info("Fetching abgeschlossen in %.1fs — %d Roheinträge", elapsed, len(all_postings))

        # --- Career pages ---
        companies_path = SKILL_DIR / "companies.yaml"
        companies = load_companies(companies_path)

        if companies:
            logger.info("Crawle %d Karriereseiten...", len(companies))
            from career_pages import CareerPagesCrawler
            crawler = CareerPagesCrawler(companies, config)
            career_postings = await crawler.fetch_all(queries)
            save_raw_data(career_postings, "career_pages", timestamp)
            all_postings.extend(career_postings)
            logger.info("Karriereseiten: %d Jobs", len(career_postings))

    # --- Deduplizierung ---
    logger.info("Deduplizierung...")
    unique_postings = deduplicate(all_postings)
    logger.info("Nach Deduplizierung: %d unique Jobs (von %d)", len(unique_postings), len(all_postings))

    # Limit
    if len(unique_postings) > max_jobs:
        unique_postings = unique_postings[:max_jobs]
        logger.info("Auf %d Jobs begrenzt", max_jobs)

    # --- Scoring ---
    logger.info("Scoring...")
    scorer = load_scorer(config, str(SKILL_DIR / "scoring.yaml"))
    scored_postings = [scorer.score(p) for p in unique_postings]
    scored_postings.sort(key=lambda p: p.score, reverse=True)

    above_zero = sum(1 for p in scored_postings if p.score > 0)
    logger.info("Score-Verteilung: %d positiv, %d negativ/null",
                above_zero, len(scored_postings) - above_zero)

    # --- Verifizierung (Top N) ---
    if not args.no_verify and top_verify > 0:
        top_to_verify = scored_postings[:top_verify]
        rest = scored_postings[top_verify:]
        logger.info("Verifiziere Top-%d Jobs...", len(top_to_verify))
        verifier = Verifier(config)
        verified_top = await verifier.verify_all(top_to_verify)
        scored_postings = verified_top + rest
    else:
        logger.info("Verifizierung übersprungen")

    # --- Excel-Export ---
    logger.info("Erstelle Excel-Datei...")
    output_path = export_to_excel(scored_postings, str(output_path), config)

    # --- Summary ---
    logger.info("=" * 60)
    logger.info("FERTIG!")
    logger.info("Jobs gesamt: %d", len(scored_postings))
    logger.info("Excel-Datei: %s", output_path)
    logger.info("=" * 60)

    # Print top 10 to console
    print("\n" + "=" * 60)
    print(f"Job-Hunter abgeschlossen — {len(scored_postings)} Jobs gefunden")
    print(f"Excel: {output_path}")
    print("=" * 60)
    print("\nTop 10 Jobs:\n")

    for i, p in enumerate(scored_postings[:10], 1):
        remote_icon = "🌐" if p.remote_type == "full_remote" else "🏢"
        print(f"{i:2}. [{p.score:+.0f}] {remote_icon} {p.title}")
        print(f"     {p.company} | {p.location}")
        print(f"     {p.application_url[:80]}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Job-Hunter — Automatisierte Job-Recherche",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Pfad zur config.yaml")
    parser.add_argument("--max-jobs", type=int, help="Maximale Anzahl Jobs")
    parser.add_argument("--output", help="Output-Pfad für Excel-Datei")
    parser.add_argument("--resume", action="store_true", help="Nutze gecachte Rohdaten")
    parser.add_argument("--sources", help="Kommaseparierte Quellenliste")
    parser.add_argument("--top", type=int, help="Anzahl Jobs für Verifizierung")
    parser.add_argument("--no-verify", action="store_true", help="Verifizierung überspringen")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
