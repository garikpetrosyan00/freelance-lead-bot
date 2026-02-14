"""SQLite backup and retention helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app import db


def backup_db(dest_path: str) -> str:
    """Create a safe SQLite snapshot using the online backup API."""
    raw = (dest_path or "").strip()
    if not raw:
        raise ValueError("dest_path is required")

    final_path = Path(raw).expanduser().resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    with db._connect() as source_conn:
        with sqlite3.connect(str(final_path)) as dest_conn:
            source_conn.backup(dest_conn)
            dest_conn.commit()

    return str(final_path)


def rotate_backups(dir_path: str, keep_last: int = 14) -> None:
    """Keep only the newest N .db backups in a directory."""
    safe_keep = max(1, int(keep_last))
    backup_dir = Path(dir_path).expanduser().resolve()
    if not backup_dir.exists() or not backup_dir.is_dir():
        return

    files = [
        path
        for path in backup_dir.iterdir()
        if path.is_file() and path.suffix == ".db"
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)

    for stale in files[safe_keep:]:
        try:
            os.remove(stale)
        except FileNotFoundError:
            continue
