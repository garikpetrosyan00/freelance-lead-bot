"""Lead matching utilities."""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from app.leads import Lead

SYNONYMS = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "node": "node.js",
    "reactjs": "react",
    "postgres": "postgresql",
}


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _split_compound(token: str) -> List[str]:
    if "." in token or "-" in token:
        parts = re.split(r"[.-]", token)
        return [part for part in parts if part]
    return []


def tokenize(text: str) -> Set[str]:
    normalized = normalize_text(text)
    tokens: Set[str] = set()
    pattern = r"c\+\+|c#|[a-z0-9]+(?:[.-][a-z0-9]+)+|[a-z0-9]+"
    for match in re.finditer(pattern, normalized):
        token = match.group(0)
        tokens.add(token)
        if token in {"c++", "c#"}:
            tokens.add("c")
            continue
        if "." in token or "-" in token:
            tokens.update(_split_compound(token))

    return _expand_synonyms(tokens)


def _expand_synonyms(tokens: Set[str]) -> Set[str]:
    expanded = set(tokens)
    for token in tokens:
        if token in SYNONYMS:
            expanded.add(SYNONYMS[token])
    return expanded


def _tokenize_base(text: str) -> Set[str]:
    normalized = normalize_text(text)
    tokens: Set[str] = set()
    pattern = r"c\+\+|c#|[a-z0-9]+(?:[.-][a-z0-9]+)+|[a-z0-9]+"
    for match in re.finditer(pattern, normalized):
        token = match.group(0)
        tokens.add(token)
        if token in {"c++", "c#"}:
            tokens.add("c")
            continue
        if "." in token or "-" in token:
            tokens.update(_split_compound(token))
    return tokens


def _skill_weight(skill: str) -> int:
    word_count = len(skill.strip().split())
    if word_count <= 1:
        return 1
    if word_count == 2:
        return 2
    return 3


def match_lead(user_skills: list[str], lead: Lead) -> Dict[str, object]:
    if not user_skills:
        return {"matched": [], "score": 0, "level": "NONE", "details": []}

    blob = f"{lead.title} {lead.description}"
    lead_base_tokens = _tokenize_base(blob)
    lead_tokens = _expand_synonyms(set(lead_base_tokens))

    matched: List[str] = []
    details: List[str] = []
    total_weight = 0
    matched_weight = 0

    for skill in user_skills:
        weight = _skill_weight(skill)
        total_weight += weight

        skill_base_tokens = _tokenize_base(skill)
        if not skill_base_tokens:
            continue
        skill_tokens = _expand_synonyms(set(skill_base_tokens))

        if skill_tokens.issubset(lead_tokens):
            matched.append(skill)
            matched_weight += weight
            if len(skill.strip().split()) == 1:
                details.append(f"Matched keyword: {skill.strip().lower()}")
            else:
                details.append(f"Matched phrase: {skill.strip().lower()}")

            for token in skill_base_tokens:
                if token in SYNONYMS:
                    synonym = SYNONYMS[token]
                    if token not in lead_base_tokens and synonym in lead_base_tokens:
                        details.append(f"Synonym match: {token} → {synonym}")

    if total_weight == 0:
        score = 0
    else:
        score = round(100 * matched_weight / total_weight)
    score = max(0, min(100, score))

    if score == 0:
        level = "NONE"
    elif score <= 33:
        level = "LOW"
    elif score <= 66:
        level = "MEDIUM"
    else:
        level = "HIGH"

    return {"matched": matched, "score": score, "level": level, "details": details}
