"""CLI helper for safe SQLite backups with retention."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.ops.backup import backup_db, rotate_backups


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a safe SQLite backup snapshot")
    parser.add_argument("--out-dir", default="./backups", help="Destination directory for backups")
    parser.add_argument("--keep", type=int, default=14, help="Number of newest backups to keep")
    parser.add_argument("--prefix", default="botdb", help="Filename prefix")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{args.prefix}_{now}.db"
    out_dir = Path(args.out_dir).expanduser().resolve()
    target = out_dir / filename

    try:
        final_path = backup_db(str(target))
        rotate_backups(str(out_dir), keep_last=args.keep)
    except Exception as exc:
        print(f"backup_db: FAILED: {exc}")
        return 1

    print(f"backup_db: OK: {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
