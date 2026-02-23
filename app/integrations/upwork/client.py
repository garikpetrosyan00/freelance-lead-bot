"""Upwork OAuth2 + GraphQL client helpers."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app import db
from app.config import (
    get_upwork_client_id,
    get_upwork_client_secret,
    get_upwork_graphql_url,
    get_upwork_oauth_authorize_url,
    get_upwork_oauth_token_url,
    get_upwork_redirect_url,
)
from app.ops.crypto import decrypt_str, encrypt_str

logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
_TOKEN_REFRESH_SKEW_SECONDS = 5 * 60
_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)
_REFRESH_LOCKS: dict[int, asyncio.Lock] = {}


class UpworkClientError(RuntimeError):
    """Base error for Upwork client operations."""


class UpworkAuthError(UpworkClientError):
    """Persistent authentication/authorization error (401/403)."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"upwork auth error http {self.status_code}")


class UpworkRateLimitError(UpworkClientError):
    """Rate-limit response from Upwork APIs."""

    def __init__(self, status_code: int = 429) -> None:
        self.status_code = int(status_code)
        super().__init__(f"upwork rate limit http {self.status_code}")


class UpworkTransientError(UpworkClientError):
    """Transient upstream/network/service error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"upwork transient error http {self.status_code}")


@dataclass(slots=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    obtained_at_iso: str
    expires_at_iso: str


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _token_bundle_from_payload(payload: dict[str, Any]) -> TokenBundle:
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    token_type = str(payload.get("token_type") or "Bearer").strip() or "Bearer"
    expires_in_raw = payload.get("expires_in")
    try:
        expires_in = int(expires_in_raw)
    except (TypeError, ValueError):
        expires_in = 0
    if not access_token:
        raise RuntimeError("Upwork token response missing access_token.")
    now = _utc_now_dt()
    expires_at = now + timedelta(seconds=max(0, expires_in))
    return TokenBundle(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_type,
        expires_in=max(0, expires_in),
        obtained_at_iso=now.isoformat(),
        expires_at_iso=expires_at.isoformat(),
    )


async def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.post(url, data=data)
    if response.status_code >= 400:
        raise RuntimeError(f"Upwork OAuth endpoint error: HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("Failed to parse Upwork OAuth response JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Upwork OAuth response JSON has unexpected shape.")
    return payload


def build_authorize_url(state: str) -> str:
    """Build OAuth2 authorization URL."""
    params = {
        "response_type": "code",
        "client_id": get_upwork_client_id(),
        "redirect_uri": get_upwork_redirect_url(),
        "state": str(state),
    }
    return f"{get_upwork_oauth_authorize_url()}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> TokenBundle:
    """Exchange OAuth2 authorization code for access and refresh tokens."""
    payload = await _post_form(
        get_upwork_oauth_token_url(),
        {
            "grant_type": "authorization_code",
            "client_id": get_upwork_client_id(),
            "client_secret": get_upwork_client_secret(),
            "redirect_uri": get_upwork_redirect_url(),
            "code": str(code).strip(),
        },
    )
    return _token_bundle_from_payload(payload)


async def refresh_access_token(refresh_token: str) -> TokenBundle:
    """Refresh access token using a refresh token."""
    payload = await _post_form(
        get_upwork_oauth_token_url(),
        {
            "grant_type": "refresh_token",
            "client_id": get_upwork_client_id(),
            "client_secret": get_upwork_client_secret(),
            "refresh_token": str(refresh_token).strip(),
        },
    )
    return _token_bundle_from_payload(payload)


def _get_refresh_lock(user_id: int) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(int(user_id))
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[int(user_id)] = lock
    return lock


def _is_refresh_needed(account: dict[str, Any]) -> bool:
    expires_at = _parse_iso_utc(str(account.get("access_token_expires_at") or "") or None)
    now = _utc_now_dt()
    return expires_at is None or expires_at <= now + timedelta(seconds=_TOKEN_REFRESH_SKEW_SECONDS)


def _require_connected_account(user_id: int, account: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved = account if account is not None else db.upwork_oauth_get_account(user_id)
    if not resolved or int(resolved.get("is_connected") or 0) != 1:
        raise RuntimeError("Upwork account is not connected.")
    access_token_enc = str(resolved.get("access_token_enc") or "").strip()
    refresh_token_enc = str(resolved.get("refresh_token_enc") or "").strip()
    if not access_token_enc or not refresh_token_enc:
        raise RuntimeError("Connected Upwork account has missing tokens.")
    return resolved


def _decrypt_account_tokens(account: dict[str, Any]) -> tuple[str, str]:
    access_token_enc = str(account.get("access_token_enc") or "").strip()
    refresh_token_enc = str(account.get("refresh_token_enc") or "").strip()
    return decrypt_str(access_token_enc), decrypt_str(refresh_token_enc)


async def _refresh_for_user(user_id: int, account: dict[str, Any]) -> str:
    account = _require_connected_account(user_id, account=account)
    refresh_token_enc = str(account.get("refresh_token_enc") or "").strip()
    if not refresh_token_enc:
        raise RuntimeError("No stored refresh token for connected Upwork account.")
    refresh_plain = decrypt_str(refresh_token_enc)
    bundle = await refresh_access_token(refresh_plain)
    next_refresh = bundle.refresh_token or refresh_plain
    db.upwork_oauth_upsert_account_connected(
        user_id=user_id,
        access_token_enc=encrypt_str(bundle.access_token),
        refresh_token_enc=encrypt_str(next_refresh),
        expires_at_iso=bundle.expires_at_iso,
        scopes=str(account.get("scopes") or "") or None,
        tenant_id=str(account.get("tenant_id") or "") or None,
    )
    return bundle.access_token


async def _get_or_refresh_access_token_locked(
    user_id: int,
    *,
    force_refresh: bool,
    failed_access_token: str | None = None,
) -> str:
    async with _get_refresh_lock(user_id):
        account = _require_connected_account(user_id)
        access_plain, _refresh_plain = _decrypt_account_tokens(account)
        if not force_refresh and not _is_refresh_needed(account):
            return access_plain
        if force_refresh and failed_access_token and access_plain != failed_access_token:
            # Another concurrent request already rotated token; reuse updated value.
            return access_plain
        return await _refresh_for_user(user_id, account)


async def get_valid_access_token_for_user(user_id: int) -> str:
    """Get valid access token for user; refresh if near expiry."""
    account = _require_connected_account(user_id)
    access_plain, _refresh_plain = _decrypt_account_tokens(account)
    if _is_refresh_needed(account):
        return await _get_or_refresh_access_token_locked(user_id, force_refresh=False)
    return access_plain


async def graphql_request(user_id: int, gql: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute authenticated Upwork GraphQL request with retries and token refresh."""
    account = _require_connected_account(user_id)

    refreshed_after_401 = False
    access_token = await get_valid_access_token_for_user(user_id)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            headers: dict[str, str] = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            tenant_id = str(account.get("tenant_id") or "").strip()
            if tenant_id:
                headers["X-Upwork-API-TenantId"] = tenant_id

            response = await client.post(
                get_upwork_graphql_url(),
                json={"query": gql, "variables": variables},
                headers=headers,
            )

            if response.status_code in {401, 403} and not refreshed_after_401:
                account = db.upwork_oauth_get_account(user_id) or account
                access_token = await _get_or_refresh_access_token_locked(
                    user_id,
                    force_refresh=True,
                    failed_access_token=access_token,
                )
                refreshed_after_401 = True
                continue
            if response.status_code in {401, 403}:
                raise UpworkAuthError(response.status_code)
            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt < len(_RETRY_DELAYS_SECONDS):
                    delay = _RETRY_DELAYS_SECONDS[attempt] + random.uniform(0.0, 0.2)
                    await asyncio.sleep(delay)
                    continue
                if response.status_code == 429:
                    raise UpworkRateLimitError(response.status_code)
                raise UpworkTransientError(response.status_code)
            if response.status_code >= 400:
                raise RuntimeError(f"Upwork GraphQL HTTP error: {response.status_code}")

            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError("Failed to parse Upwork GraphQL JSON response.") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Upwork GraphQL response has unexpected shape.")

            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                # Many GraphQL auth failures are returned in `errors` with HTTP 200.
                if (not refreshed_after_401) and any("unauth" in str(item).lower() for item in errors):
                    account = db.upwork_oauth_get_account(user_id) or account
                    access_token = await _get_or_refresh_access_token_locked(
                        user_id,
                        force_refresh=True,
                        failed_access_token=access_token,
                    )
                    refreshed_after_401 = True
                    continue
                if refreshed_after_401 and any("unauth" in str(item).lower() for item in errors):
                    raise UpworkAuthError(401)
                msg = str(errors[0]) if errors else "unknown GraphQL error"
                raise RuntimeError(f"Upwork GraphQL returned errors: {msg}")

            return payload
    logger.warning("graphql_request exhausted retries user_id=%s", user_id)
    raise RuntimeError("Upwork GraphQL request failed after retries.")


def _safe_snippet(text: str | None, max_len: int = 220) -> str | None:
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1].rstrip() + "…"


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "rawValue"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)):
                return float(candidate)
    return None


def _extract_jobs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    root = data.get("publicMarketplaceJobPostingsSearch")
    if not isinstance(root, dict):
        return []
    if isinstance(root.get("jobs"), list):
        return [row for row in root.get("jobs", []) if isinstance(row, dict)]
    if isinstance(root.get("results"), list):
        return [row for row in root.get("results", []) if isinstance(row, dict)]
    edges = root.get("edges")
    if isinstance(edges, list):
        out: list[dict[str, Any]] = []
        for edge in edges:
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                out.append(edge["node"])
        return out
    return []


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    ciphertext = str(job.get("ciphertext") or "").strip()
    title = str(job.get("title") or "").strip() or "Untitled"
    url = str(job.get("url") or "").strip()
    if not url and ciphertext:
        url = f"https://www.upwork.com/jobs/~{ciphertext}"
    client_obj = job.get("client")
    client_payment_verified = None
    if isinstance(client_obj, dict):
        for key in ("paymentVerified", "isPaymentMethodVerified", "paymentVerificationStatus"):
            if key in client_obj:
                value = client_obj.get(key)
                client_payment_verified = value
                break
    return {
        "id": str(job.get("id") or ciphertext or ""),
        "title": title,
        "url": url or None,
        "snippet": _safe_snippet(
            str(job.get("snippet") or "") or str(job.get("description") or "")
        ),
        "published_at": str(job.get("publishedDateTime") or job.get("createdDateTime") or "") or None,
        "job_type": str(job.get("type") or job.get("jobType") or "") or None,
        "budget": _first_number(job.get("amount") or job.get("budget")),
        "hourly_from": _first_number(job.get("hourlyBudgetMin") or job.get("hourlyRateMin")),
        "hourly_to": _first_number(job.get("hourlyBudgetMax") or job.get("hourlyRateMax")),
        "client_payment_verified": client_payment_verified,
    }


async def search_public_jobs(user_id: int, query_text: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search public marketplace jobs via official Upwork GraphQL API."""
    safe_limit = max(1, min(int(limit), 50))
    gql = """
    query SearchJobs($marketPlaceJobFilter: PublicMarketplaceJobPostingsSearchFilter!) {
      publicMarketplaceJobPostingsSearch(marketPlaceJobFilter: $marketPlaceJobFilter) {
        jobs {
          id
          title
          ciphertext
          description
          createdDateTime
          publishedDateTime
          type
          amount {
            amount
          }
          hourlyBudgetMin
          hourlyBudgetMax
          client {
            paymentVerified
            isPaymentMethodVerified
            paymentVerificationStatus
          }
        }
      }
    }
    """
    filter_candidates = (
        {"query": query_text, "paging": {"offset": 0, "count": safe_limit}},
        {"searchTerm": query_text, "paging": {"offset": 0, "count": safe_limit}},
        {"q": query_text, "paging": {"offset": 0, "count": safe_limit}},
        {"query": query_text},
        {"searchTerm": query_text},
    )
    last_error: Exception | None = None
    for market_filter in filter_candidates:
        try:
            payload = await graphql_request(
                user_id=user_id,
                gql=gql,
                variables={"marketPlaceJobFilter": market_filter},
            )
            rows = _extract_jobs(payload)
            return [_normalize_job(row) for row in rows[:safe_limit]]
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError("Upwork public jobs query failed.") from last_error
