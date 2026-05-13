from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class RemoteType(str, Enum):
    full_remote = "full_remote"
    hybrid = "hybrid"
    onsite = "onsite"
    unknown = "unknown"


class EmploymentType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    freelance = "freelance"
    unknown = "unknown"


class SkillLevel(str, Enum):
    expert = "expert"
    applied = "applied"
    basic = "basic"


_OUTBOUND_KEYWORDS = [
    "cold calling", "cold call", "kaltakquise", "outbound sales",
    "cold outreach", "door to door", "lead generation cold",
    "pipeline generation", "hunting new business", "new logo",
]

_PHONE_KEYWORDS = [
    "phone support", "telephone support", "inbound calls",
    "call center", "callcenter", "on the phone", "customer calls",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)


class JobPosting(BaseModel):
    id: str = Field(default="", description="SHA256 hash for deduplication")
    source: str = Field(..., description="Source adapter name")
    company: str = Field(default="")
    title: str = Field(default="")
    location: str = Field(default="")
    remote_type: RemoteType = Field(default=RemoteType.unknown)
    country: str = Field(default="")
    employment_type: EmploymentType = Field(default=EmploymentType.unknown)
    hours_per_week: int | None = Field(default=None)
    salary_min: float | None = Field(default=None)
    salary_max: float | None = Field(default=None)
    salary_currency: str = Field(default="EUR")
    salary_period: str = Field(default="yearly")
    languages_required: list[str] = Field(default_factory=list)
    description_text: str = Field(default="")
    description_html: str = Field(default="")
    posted_date: date | None = Field(default=None)
    application_url: str = Field(default="")
    requires_phone: bool = Field(default=False)
    requires_cold_calling: bool = Field(default=False)
    skills_mentioned: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0)
    score_details: dict[str, float] = Field(default_factory=dict)
    is_verified: bool = Field(default=False)
    is_active: bool | None = Field(default=None)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def compute_id_and_flags(self) -> "JobPosting":
        if not self.id:
            key = f"{self.application_url}|{self.title}|{self.company}"
            self.id = hashlib.sha256(key.encode()).hexdigest()[:16]

        combined = f"{self.title} {self.description_text}".lower()
        if not self.requires_cold_calling:
            self.requires_cold_calling = _contains_any(combined, _OUTBOUND_KEYWORDS)
        if not self.requires_phone:
            self.requires_phone = _contains_any(combined, _PHONE_KEYWORDS)

        if not self.hours_per_week:
            self.hours_per_week = _extract_hours(combined)

        return self

    def richness_score(self) -> int:
        """Used during deduplication to prefer the most complete record."""
        return sum([
            bool(self.salary_min),
            bool(self.salary_max),
            bool(self.description_text),
            bool(self.posted_date),
            bool(self.hours_per_week),
            len(self.languages_required),
            len(self.skills_mentioned),
        ])

    def to_excel_row(self) -> dict[str, Any]:
        return {
            "Score": round(self.score, 1),
            "Firma": self.company,
            "Titel": self.title,
            "Standort": self.location,
            "Land": self.country,
            "Remote": self.remote_type,
            "Arbeitszeit": self.employment_type,
            "Stunden/Woche": self.hours_per_week,
            "Gehalt Min": self.salary_min,
            "Gehalt Max": self.salary_max,
            "Währung": self.salary_currency,
            "Kaltakquise": "Ja" if self.requires_cold_calling else "Nein",
            "Telefon": "Ja" if self.requires_phone else "Nein",
            "Sprachen": ", ".join(self.languages_required),
            "Skills": ", ".join(self.skills_mentioned[:10]),
            "Verifiziert": "Ja" if self.is_verified else "Nein",
            "Aktiv": ("Ja" if self.is_active else "Nein") if self.is_active is not None else "?",
            "Veröffentlicht": str(self.posted_date) if self.posted_date else "",
            "Quelle": self.source,
            "URL": self.application_url,
        }


def _extract_hours(text: str) -> int | None:
    patterns = [
        r"(\d{2})\s*(?:hours?|h)/week",
        r"(\d{2})\s*(?:stunden?|h)/woche",
        r"(\d{2})-(?:\d{2})\s*(?:hours?|stunden?)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = int(m.group(1))
            if 10 <= val <= 60:
                return val
    return None
