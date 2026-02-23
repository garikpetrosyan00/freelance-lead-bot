"""Run OAuth web app locally: `python -m app.web`."""

from __future__ import annotations

import uvicorn


def main() -> int:
    uvicorn.run("app.web.main:app", host="0.0.0.0", port=8081, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
