"""Smoke check for required Upwork API environment variables."""

from __future__ import annotations

import os


def main() -> int:
    try:
        try:
            from dotenv import load_dotenv  # type: ignore

            load_dotenv()
        except Exception:
            pass

        required = (
            "UPWORK_CLIENT_ID",
            "UPWORK_CLIENT_SECRET",
            "UPWORK_REDIRECT_URL",
            "UPWORK_TOKEN_ENCRYPTION_KEY",
        )
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            print(f"FAILED: missing VARS {', '.join(missing)}")
            return 1
        print("smoke_upwork_client_env: OK")
        return 0
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
