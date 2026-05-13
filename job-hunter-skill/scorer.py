"""Scoring engine — assigns a match score 0-100+ to each job posting."""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from schema import JobPosting

logger = logging.getLogger(__name__)


class Scorer:
    def __init__(self, config: dict[str, Any], scoring_rules: list[dict[str, Any]]):
        self.config = config
        self.rules = scoring_rules
        self.profile_skills = {
            s["name"].lower(): s.get("level", "basic")
            for s in config.get("skills", [])
        }
        self.profile_languages = {lang.lower() for lang in config.get("languages", [])}
        salary_cfg = config.get("salary", {})
        self.salary_min = salary_cfg.get("min")
        self.salary_max = salary_cfg.get("max")
        self.prefer_sme = config.get("scoring", {}).get("prefer_sme", True)
        self.title_blacklist = [
            t.lower() for t in config.get("title", {}).get("blacklist", [])
        ]
        self.industry_blacklist = [
            i.lower() for i in config.get("industry", {}).get("blacklist", [])
        ]

    def score(self, posting: JobPosting) -> JobPosting:
        details: dict[str, float] = {}
        total = 0.0

        combined_text = f"{posting.title} {posting.description_text} {posting.location}".lower()

        for rule in self.rules:
            rule_id = rule.get("id", "")
            points = float(rule.get("score", 0))
            condition = rule.get("condition", "")
            keywords = [k.lower() for k in rule.get("keywords", [])]

            earned = 0.0

            if keywords:
                if any(kw in combined_text for kw in keywords):
                    earned = points

            elif condition == "language_in_required":
                if self._language_matches(combined_text):
                    earned = points

            elif condition == "skill_in_description":
                matched = self._count_skill_matches(combined_text)
                earned = points * matched

            elif condition == "salary_above_minimum":
                if self._salary_above_min(posting):
                    earned = points

            elif condition == "salary_below_minimum":
                if self._salary_below_min(posting):
                    earned = points  # negative

            elif condition == "company_is_sme":
                if self.prefer_sme and self._is_sme(posting.description_text):
                    earned = points

            elif condition == "country_not_in_preferences":
                if not self._country_ok(posting.country):
                    earned = points  # negative

            elif condition == "industry_in_blacklist":
                if self._industry_blacklisted(combined_text):
                    earned = points  # negative

            elif condition == "title_in_blacklist":
                if self._title_blacklisted(posting.title):
                    earned = -100.0

            if earned != 0:
                details[rule_id] = earned
                total += earned

        # Hard penalties override everything
        if posting.requires_cold_calling:
            details["cold_calling_detected"] = -50.0
            total -= 50.0
        if posting.requires_phone:
            details["phone_detected"] = -30.0
            total -= 30.0

        posting.score = round(total, 1)
        posting.score_details = details

        # Extract skills mentioned in description
        if not posting.skills_mentioned:
            posting.skills_mentioned = self._extract_skill_mentions(combined_text)

        return posting

    def _language_matches(self, text: str) -> bool:
        if not self.profile_languages:
            return True
        for lang in self.profile_languages:
            lang_keywords = {
                "de": ["deutsch", "german", "auf deutsch", "deutschkenntnisse"],
                "en": ["english", "englisch", "englishkenntnisse"],
                "fr": ["french", "français", "französisch"],
                "es": ["spanish", "español", "spanisch"],
            }
            keywords = lang_keywords.get(lang, [lang])
            if any(kw in text for kw in keywords):
                return True
        return False

    def _count_skill_matches(self, text: str) -> int:
        count = 0
        for skill_name in self.profile_skills:
            if skill_name in text:
                count += 1
        return count

    def _extract_skill_mentions(self, text: str) -> list[str]:
        return [
            skill for skill in self.profile_skills
            if skill in text
        ][:15]

    def _salary_above_min(self, posting: JobPosting) -> bool:
        if self.salary_min is None:
            return False
        if posting.salary_max and posting.salary_max >= self.salary_min:
            return True
        if posting.salary_min and posting.salary_min >= self.salary_min:
            return True
        return False

    def _salary_below_min(self, posting: JobPosting) -> bool:
        if self.salary_min is None:
            return False
        if posting.salary_max and posting.salary_max < self.salary_min:
            return True
        return False

    def _is_sme(self, description: str) -> bool:
        sme_signals = [
            "startup", "scale-up", "scaleup", "growing team", "small team",
            "series a", "series b", "seed", "50 employees", "100 employees",
        ]
        large_co_signals = [
            "fortune 500", "dax", "global corporation", "multinational",
            "10,000 employees", "50,000 employees",
        ]
        lower = description.lower()
        if any(s in lower for s in large_co_signals):
            return False
        if any(s in lower for s in sme_signals):
            return True
        return False

    def _country_ok(self, country: str) -> bool:
        if not country:
            return True
        preferred = self.config.get("location", {}).get("countries", [])
        if not preferred:
            return True
        return country.upper() in [c.upper() for c in preferred]

    def _industry_blacklisted(self, text: str) -> bool:
        return any(bl in text for bl in self.industry_blacklist)

    def _title_blacklisted(self, title: str) -> bool:
        lower = title.lower()
        return any(bl in lower for bl in self.title_blacklist)


def load_scorer(config: dict[str, Any], scoring_yaml_path: str) -> Scorer:
    try:
        with open(scoring_yaml_path) as f:
            scoring_config = yaml.safe_load(f)
        rules = scoring_config.get("rules", [])
    except FileNotFoundError:
        logger.warning("scoring.yaml not found — using default rules")
        rules = []
    return Scorer(config, rules)
