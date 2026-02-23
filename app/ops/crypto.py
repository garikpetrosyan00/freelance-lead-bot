"""Small Fernet helpers for token encryption at rest."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cryptography.fernet import Fernet

_KEY_ENV_NAME = "UPWORK_TOKEN_ENCRYPTION_KEY"
_KEY_HINT = (
    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
)


def _missing_key_error() -> RuntimeError:
    return RuntimeError(
        f"{_KEY_ENV_NAME} is required for Upwork token encryption/decryption. "
        f"Generate one with: {_KEY_HINT}"
    )


@lru_cache(maxsize=1)
def load_fernet() -> Fernet:
    """Load and cache Fernet instance from UPWORK_TOKEN_ENCRYPTION_KEY."""
    raw = os.getenv(_KEY_ENV_NAME, "").strip()
    if not raw:
        raise _missing_key_error()

    try:
        from cryptography.fernet import Fernet as FernetClass
    except Exception as exc:
        raise RuntimeError(
            "cryptography package is required for Upwork token encryption. "
            "Install dependencies from requirements.txt."
        ) from exc
    try:
        return FernetClass(raw.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"{_KEY_ENV_NAME} must be a valid Fernet key. Generate one with: {_KEY_HINT}"
        ) from exc


def encrypt_str(s: str) -> str:
    """Encrypt plaintext string and return Fernet token string."""
    try:
        return load_fernet().encrypt(str(s).encode("utf-8")).decode("utf-8")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Failed to encrypt value with Fernet.") from exc


def decrypt_str(token: str) -> str:
    """Decrypt Fernet token string and return plaintext."""
    try:
        from cryptography.fernet import InvalidToken
    except Exception as exc:
        raise RuntimeError(
            "cryptography package is required for Upwork token encryption. "
            "Install dependencies from requirements.txt."
        ) from exc
    try:
        return load_fernet().decrypt(str(token).encode("utf-8")).decode("utf-8")
    except RuntimeError:
        raise
    except InvalidToken as exc:
        raise RuntimeError("Invalid encrypted token payload for Fernet decryption.") from exc
    except Exception as exc:
        raise RuntimeError("Failed to decrypt value with Fernet.") from exc


def encrypt_optional(s: Optional[str]) -> Optional[str]:
    """Encrypt optional plaintext string."""
    if s is None:
        return None
    return encrypt_str(s)


def decrypt_optional(token: Optional[str]) -> Optional[str]:
    """Decrypt optional Fernet token string."""
    if token is None:
        return None
    return decrypt_str(token)
