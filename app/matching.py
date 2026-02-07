"""Lead matching utilities."""

from __future__ import annotations

import re
from typing import Dict, List, Set

from app.leads import Lead


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def tokenize(text: str) -> Set[str]:
    normalized = normalize_text(text)
    return set(re.findall(r"[a-z0-9]+", normalized))


def match_lead(user_skills: list[str], lead: Lead) -> Dict[str, object]:
    if not user_skills:
        return {"matched": [], "score": 0, "level": "NONE"}

    blob = f"{lead.title} {lead.description}"
    blob_tokens = tokenize(blob)

    matched: List[str] = []
    for skill in user_skills:
        skill_tokens = tokenize(skill)
        if not skill_tokens:
            continue
        if skill_tokens.issubset(blob_tokens):
            matched.append(skill)

    matched_count = len(matched)
    score = round(100 * matched_count / len(user_skills))
    score = max(0, min(100, score))

    if score == 0:
        level = "NONE"
    elif score <= 33:
        level = "LOW"
    elif score <= 66:
        level = "MEDIUM"
    else:
        level = "HIGH"

    return {"matched": matched, "score": score, "level": level}
