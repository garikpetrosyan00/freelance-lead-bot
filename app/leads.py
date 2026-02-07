"""Lead model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lead:
    title: str
    description: str
    budget: str | None
    source: str
    url: str | None
