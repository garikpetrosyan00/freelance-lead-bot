"""Upwork RSS/Atom feed fetching and parsing."""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    feedparser = None

_DEFAULT_TIMEOUT_SECONDS = 10
_DEFAULT_UA = "FreelanceLeadBot/1.0 (+https://t.me/)"
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTTP_CACHE_HEADERS: dict[str, dict[str, str]] = {}


@dataclass(slots=True)
class JobItem:
    uid: str
    title: str
    link: str
    summary: str
    published_at: str | None


def _clean_summary(value: str, max_len: int = 300) -> str:
    text = html.unescape(value or "")
    text = _TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _make_uid(raw_uid: str | None, link: str) -> str:
    candidate = (raw_uid or "").strip()
    if candidate:
        return candidate
    return hashlib.sha256((link or "").encode("utf-8")).hexdigest()


def _normalize_published(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    raw = str(raw_value).strip()
    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def _fetch_feed_text(rss_url: str) -> str:
    headers = {"User-Agent": _DEFAULT_UA, "Accept": "application/rss+xml, application/atom+xml, text/xml"}
    cached = _HTTP_CACHE_HEADERS.get(rss_url) or {}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    timeout = httpx.Timeout(_DEFAULT_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(rss_url, headers=headers)
        if response.status_code == 304:
            return ""
        response.raise_for_status()
        etag = (response.headers.get("ETag") or "").strip()
        last_modified = (response.headers.get("Last-Modified") or "").strip()
        if etag or last_modified:
            _HTTP_CACHE_HEADERS[rss_url] = {"etag": etag, "last_modified": last_modified}
        return response.text


def _items_from_feedparser(feed_text: str) -> list[JobItem]:
    if feedparser is None:
        return []
    parsed = feedparser.parse(feed_text)
    items: list[JobItem] = []
    for entry in parsed.entries or []:
        title = str(getattr(entry, "title", "") or "").strip()
        link = str(getattr(entry, "link", "") or "").strip()
        if not link:
            continue
        summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
        uid = _make_uid(str(getattr(entry, "id", "") or "").strip() or None, link)
        published = _normalize_published(
            str(getattr(entry, "published", "") or getattr(entry, "updated", "") or "").strip() or None
        )
        items.append(
            JobItem(
                uid=uid,
                title=title or "Untitled",
                link=link,
                summary=_clean_summary(summary),
                published_at=published,
            )
        )
    return items


def _read_text(node: ET.Element | None, *tag_names: str) -> str:
    if node is None:
        return ""
    for tag_name in tag_names:
        for child in node.iter():
            tag = child.tag
            if isinstance(tag, str) and tag.rsplit("}", 1)[-1] == tag_name and child.text:
                return child.text.strip()
    return ""


def _items_from_xml(feed_text: str) -> list[JobItem]:
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError:
        return []

    items: list[JobItem] = []
    candidates: list[ET.Element] = []
    root_tag = root.tag.rsplit("}", 1)[-1].lower()
    if root_tag == "rss":
        candidates = [n for n in root.iter() if isinstance(n.tag, str) and n.tag.rsplit("}", 1)[-1] == "item"]
    else:
        candidates = [n for n in root.iter() if isinstance(n.tag, str) and n.tag.rsplit("}", 1)[-1] == "entry"]

    for node in candidates:
        title = _read_text(node, "title")
        summary = _read_text(node, "summary", "description", "content")
        raw_uid = _read_text(node, "id", "guid")
        link = ""
        for child in node.iter():
            if not isinstance(child.tag, str):
                continue
            if child.tag.rsplit("}", 1)[-1] != "link":
                continue
            href = (child.attrib.get("href") or "").strip()
            if href:
                link = href
                break
            if child.text and child.text.strip():
                link = child.text.strip()
                break

        if not link:
            continue

        published = _normalize_published(_read_text(node, "published", "updated", "pubDate"))
        items.append(
            JobItem(
                uid=_make_uid(raw_uid or None, link),
                title=title or "Untitled",
                link=link,
                summary=_clean_summary(summary),
                published_at=published,
            )
        )
    return items


async def fetch_feed_items(rss_url: str) -> list[JobItem]:
    feed_text = await _fetch_feed_text(rss_url)
    if not feed_text.strip():
        return []
    items = _items_from_feedparser(feed_text)
    if items:
        return items
    return _items_from_xml(feed_text)


def parse_feed_items_from_text(feed_text: str) -> list[JobItem]:
    """Helper for tests and smoke checks without network calls."""
    items = _items_from_feedparser(feed_text)
    if items:
        return items
    return _items_from_xml(feed_text)
