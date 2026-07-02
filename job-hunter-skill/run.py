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
import re
import sys
import time
from collections import Counter
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


def get_queries(config: dict[str, Any], source: str | None = None) -> list[str]:
    """Default-Queries, optional gemergt mit portal-spezifischen Synonymen."""
    queries_cfg = config.get("search_queries", {}) or {}
    default = queries_cfg.get("default", []) or []
    extra = (queries_cfg.get(source) or []) if source and source != "default" else []
    all_queries: list[str] = []
    seen: set[str] = set()
    for q in list(default) + list(extra):
        lower = q.lower()
        if lower not in seen:
            seen.add(lower)
            all_queries.append(q)
    return all_queries


COUNTRY_NAMES = {
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "MT": "Malta", "NL": "Netherlands", "UK": "United Kingdom",
    "GB": "United Kingdom", "IE": "Ireland", "FR": "France",
    "ES": "Spain", "IT": "Italy", "PT": "Portugal", "BE": "Belgium",
    "LU": "Luxembourg", "DK": "Denmark", "SE": "Sweden", "NO": "Norway",
    "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czech Republic",
    "SK": "Slovakia", "HU": "Hungary", "SI": "Slovenia", "HR": "Croatia",
    "RO": "Romania", "BG": "Bulgaria", "GR": "Greece", "CY": "Cyprus",
    "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
}


def get_locations(config: dict[str, Any]) -> list[str]:
    loc_cfg = config.get("location", {})
    countries = loc_cfg.get("countries", [])
    cities = loc_cfg.get("cities", [])
    locations: list[str] = cities.copy()
    for country in countries:
        name = COUNTRY_NAMES.get(country.upper(), country)
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


_TS_RE = re.compile(r"_(\d{8}_\d{6})$")


def _load_postings_file(json_file: Path) -> list[Any]:
    """Ein raw-JSON-File laden; ungültige Einzeleinträge werden übersprungen."""
    from schema import JobPosting

    postings: list[Any] = []
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


def latest_cache_timestamp() -> str | None:
    """Neuester Run-Timestamp über alle Dateien in data/raw."""
    raw_dir = SKILL_DIR / "data" / "raw"
    if not raw_dir.exists():
        return None
    stamps = []
    for f in raw_dir.glob("*.json"):
        m = _TS_RE.search(f.stem)
        if m:
            stamps.append(m.group(1))
    return max(stamps) if stamps else None


def load_cached_raw(timestamp: str | None = None) -> list[Any]:
    """Load previously saved raw data for --resume mode."""
    raw_dir = SKILL_DIR / "data" / "raw"
    if not raw_dir.exists():
        return []

    pattern = f"*_{timestamp}.json" if timestamp else "*.json"
    postings: list[Any] = []
    for json_file in sorted(raw_dir.glob(pattern)):
        postings.extend(_load_postings_file(json_file))
    return postings


def load_cached_source(source: str, timestamp: str | None = None) -> list[Any]:
    """Neueste (oder timestamp-genaue) Cache-Datei einer einzelnen Quelle laden.

    Wird von --from-cache genutzt: Cowork schreibt via cowork_apify.py save
    nach data/raw/<source>_<TS>.json; hier laden wir genau diese Quelle,
    ohne sie live zu fetchen.
    """
    logger = logging.getLogger("job-hunter")
    raw_dir = SKILL_DIR / "data" / "raw"
    if timestamp:
        candidates = [raw_dir / f"{source}_{timestamp}.json"]
        candidates = [c for c in candidates if c.exists()]
    else:
        # TS-Format %Y%m%d_%H%M%S ist zero-padded → lexikographisch == chronologisch
        candidates = sorted(raw_dir.glob(f"{source}_*.json"))[-1:]

    if not candidates:
        logger.warning("[from-cache] Keine Cache-Datei für Quelle %r gefunden", source)
        return []

    postings = _load_postings_file(candidates[0])
    logger.info("[from-cache] %s: %d Jobs aus %s", source, len(postings), candidates[0].name)
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
        "xing": ("sources.xing", "XingAdapter"),
        "welcometothejungle": ("sources.welcometothejungle", "WelcomeToTheJungleAdapter"),
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

    # --- From-cache-Quellen (Cowork-Mode: Apify-Ergebnisse liegen schon in data/raw) ---
    from_cache: set[str] = set()
    if args.from_cache and not args.resume:
        from_cache = {s.strip() for s in args.from_cache.split(",") if s.strip()}
        logger.info("From-Cache-Quellen: %s", sorted(from_cache))
        for source in sorted(from_cache):
            all_postings.extend(load_cached_source(source, args.cache_timestamp))

    # --- Resume mode ---
    if args.resume:
        if args.resume == "all":
            resume_ts = None
        elif args.resume == "latest":
            resume_ts = latest_cache_timestamp()
        else:
            resume_ts = args.resume
        logger.info("Resume-Modus: Lade gecachte Rohdaten (Timestamp: %s)...",
                    resume_ts or "alle")
        all_postings = load_cached_raw(resume_ts)
        logger.info("Geladen: %d Jobs aus Cache", len(all_postings))
    else:
        # --- Determine enabled sources ---
        if args.sources:
            enabled = [s.strip() for s in args.sources.split(",")]
        else:
            # xing + welcometothejungle sind Apify-gated: ohne apify.actors-Eintrag
            # in der Config überspringen sie sich selbst (kein Kostenrisiko).
            enabled = list({
                "arbeitnow", "remoteok", "remotive", "weworkremotely", "jobicy",
                "workingnomads", "4dayweek", "jobgether", "germantechjobs",
                "berlinstartupjobs", "relocateme", "justremote", "workwide",
                "meetfrank", "jobsinmalta", "konnekt", "jobsforgermans",
                "stepstone", "indeed", "linkedin", "glassdoor",
                "xing", "welcometothejungle",
            })

        # From-cache-Quellen nicht nochmal live fetchen
        enabled = [s for s in enabled if s not in from_cache]

        adapters = build_adapters(enabled, config)
        logger.info("Aktive Quellen (%d): %s", len(adapters), [a.name for a in adapters])

        # --- Fetch from all source adapters in parallel (with concurrency limit) ---
        semaphore = asyncio.Semaphore(5)

        async def limited_fetch(adapter):
            async with semaphore:
                # Default-Queries + portal-spezifische Synonyme (search_queries.<name>)
                adapter_queries = get_queries(config, adapter.name)
                return await fetch_from_adapter(adapter, adapter_queries, locations, timestamp, logger)

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
        companies = [] if "career_pages" in from_cache else load_companies(companies_path)

        if companies:
            logger.info("Crawle %d Karriereseiten...", len(companies))
            from career_pages import CareerPagesCrawler
            crawler = CareerPagesCrawler(companies, config)
            career_postings = await crawler.fetch_all(queries)
            save_raw_data(career_postings, "career_pages", timestamp)
            all_postings.extend(career_postings)
            logger.info("Karriereseiten: %d Jobs", len(career_postings))

    # --- GIGO-Filter (Mindestqualität: Titel + Firma + URL) ---
    valid_postings = [p for p in all_postings if p.is_valid]
    gigo_dropped = len(all_postings) - len(valid_postings)
    if gigo_dropped:
        logger.info("GIGO-Filter: %d von %d Einträgen entfernt (Titel/Firma/URL fehlt)",
                    gigo_dropped, len(all_postings))
    else:
        logger.info("GIGO-Filter: alle %d Einträge valid", len(all_postings))
    all_postings = valid_postings

    # --- Geo-Filter (EU/UK strict) ---
    if config.get("location", {}).get("strict_eu_uk_only"):
        from geo import is_european

        kept: list[Any] = []
        geo_reasons: Counter = Counter()
        for p in all_postings:
            ok, reason = is_european(p)
            if ok:
                kept.append(p)
            else:
                geo_reasons[reason] += 1
        geo_removed = len(all_postings) - len(kept)
        if geo_removed:
            logger.warning("Geo-Filter (EU/UK strict): %d von %d Jobs entfernt",
                           geo_removed, len(all_postings))
            for reason, count in geo_reasons.most_common():
                logger.info("  %-52s %4d", reason, count)
        else:
            logger.info("Geo-Filter (EU/UK strict): alle %d Jobs OK", len(all_postings))
        all_postings = kept

    # --- Company-Filter (KMU-Größe + Firmen-Blacklist) ---
    company_cfg = config.get("company") or {}
    if company_cfg and company_cfg.get("enabled", True):
        from company_filter import is_acceptable_company

        kept = []
        company_reasons: Counter = Counter()
        for p in all_postings:
            ok, reason = is_acceptable_company(p, company_cfg)
            if ok:
                kept.append(p)
            else:
                company_reasons[reason] += 1
        company_removed = len(all_postings) - len(kept)
        if company_removed:
            logger.warning("Company-Filter (max %s MA): %d von %d Jobs entfernt",
                           company_cfg.get("max_employees", 500),
                           company_removed, len(all_postings))
            for reason, count in company_reasons.most_common():
                logger.info("  %-52s %4d", reason, count)
        else:
            logger.info("Company-Filter: alle %d Jobs OK", len(all_postings))
        all_postings = kept

    # --- Quellen-Statistik ---
    per_source: Counter = Counter(p.source for p in all_postings)
    if per_source:
        logger.info("Einträge pro Quelle nach Validierung:")
        for source, count in per_source.most_common():
            logger.info("  %-22s %4d", source, count)

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
    parser.add_argument(
        "--resume", nargs="?", const="latest", default=None, metavar="TS",
        help="Nutze gecachte Rohdaten statt zu fetchen. Ohne Wert: neuester Run; "
             "'all': alle Cache-Dateien; sonst exakter Timestamp (z.B. 20260513_210926)",
    )
    parser.add_argument("--sources", help="Kommaseparierte Quellenliste")
    parser.add_argument(
        "--from-cache", metavar="SOURCES",
        help="Kommaseparierte Quellen aus data/raw laden statt live zu fetchen "
             "(Cowork-Mode: Apify-Ergebnisse wurden via cowork_apify.py save gespeichert). "
             "Alle übrigen Quellen fetchen normal.",
    )
    parser.add_argument(
        "--cache-timestamp", metavar="TS",
        help="Exakter Timestamp für --from-cache (Default: neueste Datei pro Quelle)",
    )
    parser.add_argument("--top", type=int, help="Anzahl Jobs für Verifizierung")
    parser.add_argument("--no-verify", action="store_true", help="Verifizierung überspringen")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
