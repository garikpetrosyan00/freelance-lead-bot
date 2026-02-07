"""SQLite storage for user preferences."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List

DB_PATH = os.path.join("data", "app.db")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id INTEGER PRIMARY KEY,
                skills TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _normalize_skills(raw_skills: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for skill in raw_skills:
        normalized = skill.strip().lower()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def set_skills(user_id: int, skills: list[str]) -> None:
    normalized = _normalize_skills(skills)
    skills_str = ", ".join(normalized)
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_prefs (user_id, skills, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                skills=excluded.skills,
                updated_at=excluded.updated_at
            """,
            (user_id, skills_str, timestamp),
        )
        conn.commit()


def get_skills(user_id: int) -> list[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT skills FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()

    if not row:
        return []

    skills_str = row[0] or ""
    return [skill.strip() for skill in skills_str.split(",") if skill.strip()]
