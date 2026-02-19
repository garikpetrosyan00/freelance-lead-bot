"""Skill matching for Upwork RSS jobs."""

from __future__ import annotations

import re
from typing import Iterable

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")

SYNONYMS: dict[str, list[str]] = {
    "aiogram": ["telegram bot", "telegram", "aiogram"],
    "stripe": ["stripe", "checkout", "webhook", "subscription"],
    "sqlite": ["sqlite", "sqlite3"],
}


def normalize(text: str) -> str:
    lowered = (text or "").lower()
    lowered = _NON_ALNUM_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def match_skills(job_text: str, skills: Iterable[str]) -> tuple[int, list[str]]:
    normalized_job = f" {normalize(job_text)} "
    skill_list = [str(skill or "").strip() for skill in skills if str(skill or "").strip()]
    matched: list[str] = []

    for raw_skill in skill_list:
        normalized_skill = normalize(raw_skill)
        if not normalized_skill:
            continue

        synonyms = SYNONYMS.get(normalized_skill)
        if synonyms:
            if any(f" {normalize(term)} " in normalized_job for term in synonyms):
                matched.append(raw_skill)
                continue

        if f" {normalized_skill} " in normalized_job:
            matched.append(raw_skill)

    denominator = max(1, min(len(skill_list), 6))
    score = min(100, int(100 * len(matched) / denominator))
    return score, matched
