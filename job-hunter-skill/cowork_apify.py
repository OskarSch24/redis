#!/usr/bin/env python3
"""Helper für die Cowork-Plugin-Variante: Brücke zwischen Cowork-Apify-Connector
und der Python-Pipeline.

Drei Subcommands:

  python3 cowork_apify.py inputs --source linkedin --config CFG
      → Gibt die Apify-Calls aus die der LinkedIn-Adapter machen würde —
        als JSON-Liste von {actor_id, input}. Plugin reicht jedes input-Dict
        an `mcp__Apify__call-actor` weiter.

  python3 cowork_apify.py save --source linkedin --timestamp 20260514_143000
      → Liest die rohen Apify-Items von STDIN (JSON-Array, wie es der Connector
        liefert), parst sie via Adapter-Logik, schreibt nach
        ~/.claude/skills/job-hunter/data/raw/linkedin_<timestamp>.json
        im JobPosting-Format (kompatibel mit run.py --from-cache).

  python3 cowork_apify.py list
      → Listet alle Quellen, die Apify-fähig sind (für Plugin-Workflow).

Genutzt vom Cowork-Plugin in commands/job-hunter.md, Schritt 6 (Cowork-Mode).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

SKILL_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

APIFY_ADAPTERS = {
    "linkedin": ("sources.linkedin", "LinkedInAdapter"),
    "glassdoor": ("sources.glassdoor", "GlassdoorAdapter"),
    # indeed + xing deaktiviert — Cost-Driver (siehe config.yaml).
    # Wer sie will: hier wieder einkommentieren UND apify.actors.* in config.yaml setzen.
    # "indeed": ("sources.indeed", "IndeedAdapter"),
    # "xing": ("sources.xing", "XingAdapter"),
    "stepstone": ("sources.stepstone", "StepStoneAdapter"),
    "welcometothejungle": ("sources.welcometothejungle", "WelcomeToTheJungleAdapter"),
    "jobgether": ("sources.jobgether", "JobgetherAdapter"),
}


def _load_adapter(source: str, config: dict[str, Any]):
    if source not in APIFY_ADAPTERS:
        sys.stderr.write(f"ERROR: Unknown source {source!r}. Known: {list(APIFY_ADAPTERS)}\n")
        sys.exit(2)
    mod_path, cls_name = APIFY_ADAPTERS[source]
    import importlib
    mod = importlib.import_module(mod_path)
    cls = getattr(mod, cls_name)
    return cls(config)


def _resolve_queries_locations(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    queries = config.get("search_queries", {}).get("default", []) or []
    loc_cfg = config.get("location", {}) or {}
    countries = loc_cfg.get("countries", []) or []
    cities = loc_cfg.get("cities", []) or []
    locations: list[str] = list(cities)
    country_names = {
        "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
        "MT": "Malta", "NL": "Netherlands", "UK": "United Kingdom",
        "FR": "France", "ES": "Spain", "IT": "Italy", "PT": "Portugal",
        "IE": "Ireland", "BE": "Belgium", "LU": "Luxembourg",
        "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland",
        "PL": "Poland", "CZ": "Czech Republic", "HU": "Hungary",
    }
    for c in countries:
        name = country_names.get(c.upper(), c)
        if name not in locations:
            locations.append(name)
    return queries, locations


def cmd_inputs(args: argparse.Namespace) -> int:
    """Extract the Apify call-list for a given source, without making the call."""
    import yaml
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    adapter = _load_adapter(args.source, cfg)
    queries, locations = _resolve_queries_locations(cfg)

    calls: list[dict[str, Any]] = []

    async def capture(actor_id: str, input_dict: dict[str, Any]) -> list:
        calls.append({"actor_id": actor_id, "input": input_dict})
        return []

    if not hasattr(adapter, "apify") or not adapter.apify.is_ready(adapter.name):
        sys.stderr.write(
            f"ERROR: Adapter {args.source!r} not Apify-ready "
            f"(token or actor missing in config).\n"
        )
        sys.exit(3)

    with patch.object(adapter.apify, "run_sync", side_effect=capture):
        async def run_capture():
            async with adapter:
                await adapter.fetch_all(queries, locations)
        asyncio.run(run_capture())

    json.dump(calls, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    """Read raw Apify items from STDIN, parse via adapter, save as JobPosting JSON."""
    raw_data = sys.stdin.read().strip()
    if not raw_data:
        sys.stderr.write("ERROR: No JSON on stdin (pipe the raw Apify output).\n")
        sys.exit(2)
    try:
        raw_items = json.loads(raw_data)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: Invalid JSON on stdin: {e}\n")
        sys.exit(2)
    if not isinstance(raw_items, list):
        sys.stderr.write(f"ERROR: Expected JSON array, got {type(raw_items).__name__}\n")
        sys.exit(2)

    import yaml
    config_path = args.config or (SKILL_DIR / "config.yaml")
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    adapter = _load_adapter(args.source, cfg)

    # Each Apify adapter has _parse_apify_item that converts one raw item → JobPosting.
    # Some adapters (indeed) take a country arg; we pass through if signature requires it.
    import inspect
    parse_method = getattr(adapter, "_parse_apify_item", None)
    if parse_method is None:
        sys.stderr.write(f"ERROR: Adapter {args.source!r} has no _parse_apify_item method.\n")
        sys.exit(3)

    sig = inspect.signature(parse_method)
    n_params = len(sig.parameters)

    postings = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            # Try calling with the minimum args. If adapter needs extra ctx
            # (e.g. country for indeed), fall back to passing "" / common defaults.
            if n_params == 1:
                posting = parse_method(item)
            else:
                # 2-arg signature: usually (item, country_or_location_hint)
                posting = parse_method(item, "")
            if posting is not None:
                postings.append(posting)
        except Exception as e:
            sys.stderr.write(f"WARN: parse error on item: {e}\n")
            continue

    raw_dir = SKILL_DIR / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"{args.source}_{args.timestamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [p.model_dump(mode="json") for p in postings],
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    sys.stdout.write(f"saved {len(postings)} items to {out_path}\n")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print(",".join(sorted(APIFY_ADAPTERS.keys())))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inputs = sub.add_parser("inputs", help="Get Apify call list for a source (JSON to stdout)")
    p_inputs.add_argument("--source", required=True, choices=list(APIFY_ADAPTERS.keys()))
    p_inputs.add_argument("--config", required=True, help="Path to config YAML")
    p_inputs.set_defaults(func=cmd_inputs)

    p_save = sub.add_parser("save", help="Parse raw Apify items from stdin, save as JobPosting JSON")
    p_save.add_argument("--source", required=True, choices=list(APIFY_ADAPTERS.keys()))
    p_save.add_argument("--timestamp", required=True, help="Run timestamp (e.g. 20260514_143000)")
    p_save.add_argument("--config", help="Path to config YAML (default: skill/config.yaml)")
    p_save.set_defaults(func=cmd_save)

    p_list = sub.add_parser("list", help="List Apify-enabled sources (comma-separated)")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
