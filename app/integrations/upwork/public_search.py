"""Public Upwork jobs search page parser (no OAuth)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import get_upwork_public_proxy

logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_BASE_SEARCH_URL = "https://www.upwork.com/nx/search/jobs/"
_BASE_HOST = "https://www.upwork.com"
_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "source",
    "src",
    "ref",
    "gclid",
    "fbclid",
}
_NEXT_DATA_RE = re.compile(
    r"<script[^>]*id=['\"]__NEXT_DATA__['\"][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_NEXT_DATA_ASSIGN_RE = re.compile(
    r"__NEXT_DATA__\s*=\s*(\{.*?\})\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _safe_snippet(text: str | None, max_len: int = 220) -> str | None:
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1].rstrip() + "..."


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "rawValue", "min", "max"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)):
                return float(candidate)
    return None


def _strip_tracking_query(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _TRACKING_QUERY_KEYS:
            continue
        kept.append((key, value))
    query = urlencode(kept, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _to_absolute_upwork_url(url: str | None, ciphertext: str | None = None) -> str | None:
    raw = str(url or "").strip()
    normalized: str | None = None
    if raw.startswith("/"):
        normalized = f"{_BASE_HOST}{raw}"
    elif raw.startswith("http://") or raw.startswith("https://"):
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if not host.endswith("upwork.com"):
            return None
        normalized = urlunsplit(("https", "www.upwork.com", parts.path or "/", parts.query, parts.fragment))
    ctext = str(ciphertext or "").strip()
    if not normalized and ctext:
        token = ctext if ctext.startswith("~") else f"~{ctext}"
        normalized = f"{_BASE_HOST}/jobs/{token}"
    if not normalized:
        return None
    normalized = _strip_tracking_query(normalized)
    if not normalized.startswith(_BASE_HOST):
        return None
    return normalized


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "verified"}:
            return True
        if lowered in {"false", "no", "unverified"}:
            return False
    return None


def _looks_like_job(node: dict[str, Any]) -> bool:
    title = str(node.get("title") or node.get("jobTitle") or "").strip()
    if not title:
        return False
    ciphertext = str(node.get("ciphertext") or node.get("cipherText") or "").strip() or None
    resolved_url = _to_absolute_upwork_url(
        str(node.get("url") or node.get("jobUrl") or "").strip() or None,
        ciphertext=ciphertext,
    )
    if not resolved_url or "/jobs/" not in resolved_url:
        return False
    has_text = any(str(node.get(key) or "").strip() for key in ("description", "snippet", "summary"))
    has_budget = any(
        node.get(key) is not None
        for key in ("amount", "budget", "fixedBudget", "hourlyBudgetMin", "hourlyBudgetMax", "hourlyRateMin", "hourlyRateMax")
    )
    return has_text or has_budget


def _extract_published_at(node: dict[str, Any]) -> str | None:
    for key in (
        "publishedAt",
        "publishedDateTime",
        "publishedOn",
        "createdDateTime",
        "createdAt",
        "created_on",
    ):
        raw = node.get(key)
        normalized = _normalize_published_at(raw)
        if normalized is not None:
            return normalized
    return None


def _normalize_published_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            epoch_int = int(raw)
        except Exception:
            return None
        epoch = float(epoch_int)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        except Exception:
            return None
    iso_raw = raw
    if iso_raw.endswith("Z"):
        iso_raw = f"{iso_raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(iso_raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _stable_token_from_url(url: str | None) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        path = urlsplit(raw).path
    except Exception:
        return None
    if "/jobs/" not in path:
        return None
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    token = parts[-1].strip()
    if not token:
        return None
    token = token.split("?")[0].split("#")[0].strip()
    return token or None


def _normalize_job(node: dict[str, Any]) -> dict[str, Any]:
    title = str(node.get("title") or node.get("jobTitle") or "").strip() or "Untitled"
    ciphertext = str(node.get("ciphertext") or node.get("cipherText") or "").strip() or None
    canonical_url = _to_absolute_upwork_url(
        str(node.get("url") or node.get("jobUrl") or "").strip() or None,
        ciphertext=ciphertext,
    )
    published_at = _extract_published_at(node)
    job_id = str(node.get("uid") or node.get("ciphertext") or node.get("cipherText") or node.get("jobId") or "").strip() or None
    if not job_id:
        job_id = _stable_token_from_url(canonical_url)
    if not job_id:
        job_id = str(node.get("id") or "").strip() or None
    if not job_id:
        payload = "|".join([title, str(canonical_url or "")])
        job_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    client_obj = node.get("client")
    client_payment_verified = None
    if isinstance(client_obj, dict):
        for key in ("paymentVerified", "isPaymentMethodVerified", "paymentVerificationStatus"):
            if key in client_obj:
                client_payment_verified = _coerce_bool(client_obj.get(key))
                break
    if client_payment_verified is None:
        for key in ("clientPaymentVerified", "paymentVerified"):
            if key in node:
                client_payment_verified = _coerce_bool(node.get(key))
                break

    return {
        "id": job_id,
        "title": title,
        "url": canonical_url,
        "snippet": _safe_snippet(
            str(node.get("snippet") or "")
            or str(node.get("description") or "")
            or str(node.get("summary") or "")
        ),
        "published_at": published_at,
        "job_type": str(node.get("type") or node.get("jobType") or node.get("workType") or "") or None,
        "budget": _first_number(node.get("amount") or node.get("budget") or node.get("fixedBudget")),
        "hourly_from": _first_number(node.get("hourlyBudgetMin") or node.get("hourlyRateMin")),
        "hourly_to": _first_number(node.get("hourlyBudgetMax") or node.get("hourlyRateMax")),
        "client_payment_verified": client_payment_verified,
    }


def _find_next_data_json(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(html)
    if match:
        raw = match.group(1).strip()
        if raw:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass

    assign_match = _NEXT_DATA_ASSIGN_RE.search(html)
    if assign_match:
        raw = assign_match.group(1).strip()
        if raw:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                return None
    return None


def _build_http_client(proxy_url: str | None) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": _HTTP_TIMEOUT,
        "follow_redirects": True,
    }
    if proxy_url:
        try:
            return httpx.AsyncClient(proxy=proxy_url, **kwargs)
        except TypeError:
            return httpx.AsyncClient(proxies=proxy_url, **kwargs)  # type: ignore[arg-type]
    return httpx.AsyncClient(**kwargs)


def _walk_find_jobs(obj: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack: list[Any] = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if _looks_like_job(current):
                out.append(current)
            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append(value)
    return out


async def search_public_jobs_public(query_text: str, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 50))
    params = {"q": query_text, "sort": "recency"}
    url = f"{_BASE_SEARCH_URL}?{urlencode(params)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.8",
    }
    proxy_url = get_upwork_public_proxy()
    proxy_enabled = bool(proxy_url)
    async with _build_http_client(proxy_url) as client:
        response = await client.get(url, headers=headers)
    if response.status_code in {429, 500, 502, 503, 504}:
        raise RuntimeError(f"upwork public search transient http {response.status_code}")
    if response.status_code >= 400:
        raise RuntimeError(f"upwork public search http {response.status_code}")

    payload = _find_next_data_json(response.text)
    if not payload:
        logger.info(
            "upwork public search query=%r fetched_count=%s normalized_count=%s proxy_enabled=%s",
            query_text[:80],
            0,
            0,
            proxy_enabled,
        )
        return []
    candidates = _walk_find_jobs(payload)
    fetched_count = len(candidates)

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for row in candidates:
        item = _normalize_job(row)
        job_id = str(item.get("id") or "").strip()
        if not job_id:
            continue
        item_url = str(item.get("url") or "").strip()
        if item_url and not item_url.startswith(_BASE_HOST):
            continue
        if job_id in seen_ids:
            continue
        if item_url and item_url in seen_urls:
            continue
        seen_ids.add(job_id)
        if item_url:
            seen_urls.add(item_url)
        normalized.append(item)
        if len(normalized) >= safe_limit:
            break
    logger.info(
        "upwork public search query=%r fetched_count=%s normalized_count=%s proxy_enabled=%s",
        query_text[:80],
        fetched_count,
        len(normalized),
        proxy_enabled,
    )
    return normalized
