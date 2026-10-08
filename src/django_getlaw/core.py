"""Core implementation of django-getlaw.

A single module covers configuration, HTTP fetching, and the cache-backed
service layer. Keeping everything here mirrors the small surface area of the
public API surface (`get_text`, `refresh_text`, `fetch_text`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.cache import caches

logger = logging.getLogger("django_getlaw")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GetlawError(Exception):
    """Base exception for all django-getlaw failures."""


class GetlawConfigurationError(GetlawError):
    """Raised when settings are missing or invalid (programmer error)."""


class GetlawAPIError(GetlawError):
    """Raised when the upstream getLaw API call fails (operational error)."""


# ---------------------------------------------------------------------------
# Settings access
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "KEYS": {},
    "TTL_SECONDS": 86400,
    "API_BASE_URL": "https://www.getlaw.de/api/texts/",
    "API_VERSION": "1",
    "TIMEOUT_SECONDS": 10,
    "CACHE_ALIAS": "default",
    "CACHE_KEY_PREFIX": "getlaw:",
    "USER_AGENT": None,  # filled in lazily with package version
}


def _conf() -> dict[str, Any]:
    """Return the merged GETLAW configuration dict (defaults + user overrides)."""
    user = getattr(settings, "GETLAW", {}) or {}
    if not isinstance(user, dict):
        raise GetlawConfigurationError(
            f"settings.GETLAW must be a dict (got {type(user).__name__!r})"
        )
    merged = {**_DEFAULTS, **user}
    if merged["USER_AGENT"] is None:
        # Imported lazily to avoid a circular import at module load.
        from django_getlaw import __version__

        merged["USER_AGENT"] = f"django-getlaw/{__version__}"
    return merged


def _api_key_for(text_type: str, conf: dict[str, Any]) -> str:
    keys = conf.get("KEYS") or {}
    if not isinstance(keys, dict):
        raise GetlawConfigurationError("settings.GETLAW['KEYS'] must be a dict")
    key = keys.get(text_type)
    if not key:
        raise GetlawConfigurationError(
            f"No API key configured for text type {text_type!r}. Add it to settings.GETLAW['KEYS']."
        )
    return str(key)


def configured_text_types() -> list[str]:
    """Return the list of text types for which an API key is configured."""
    keys = _conf().get("KEYS") or {}
    return sorted(t for t, v in keys.items() if v)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class _NoRedirectHandler(HTTPRedirectHandler):
    """Treat any 3xx response as an HTTPError instead of following it.

    The getLaw API redirects to its marketing site when the API key is
    invalid, so a redirect is a *failure* signal we must surface.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise HTTPError(req.full_url, code, f"Unexpected redirect to {newurl}", headers, fp)


def fetch_text(api_key: str, *, conf: dict[str, Any] | None = None) -> str:
    """Call the getLaw API for `api_key` and return the raw HTML content.

    Raises `GetlawAPIError` on any failure (network, HTTP, JSON, redirect,
    missing content). This function is stateless — no caching.
    """
    return _fetch(api_key, conf=conf)[0]


def _parse_lastupdate(value: Any) -> int | None:
    """getLaw's ``lastupdate`` (a Unix timestamp, sent as a string), or ``None``."""
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _fetch(api_key: str, *, conf: dict[str, Any] | None = None) -> tuple[str, int | None]:
    """:func:`fetch_text` plus when getLaw last changed the text (``lastupdate``)."""
    if not api_key:
        raise GetlawConfigurationError("api_key must be a non-empty string")

    cfg = conf or _conf()
    url = cfg["API_BASE_URL"].rstrip("/") + "/" + api_key
    request = Request(  # noqa: S310 - URL is fully derived from settings
        url,
        headers={
            "X-getLaw-API-Version": cfg["API_VERSION"],
            "User-Agent": cfg["USER_AGENT"],
            "Accept": "application/json",
        },
        method="GET",
    )

    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=cfg["TIMEOUT_SECONDS"]) as response:  # noqa: S310
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise GetlawAPIError(
            f"getLaw API returned HTTP {exc.code} for key ending in ...{api_key[-4:]}"
        ) from exc
    except URLError as exc:
        raise GetlawAPIError(f"getLaw API request failed: {exc.reason!r}") from exc
    except TimeoutError as exc:
        raise GetlawAPIError("getLaw API request timed out") from exc

    try:
        data = json.loads(raw.decode(charset))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GetlawAPIError("getLaw API returned a non-JSON response") from exc

    if not isinstance(data, dict):
        raise GetlawAPIError(f"getLaw API returned unexpected payload type: {type(data).__name__}")

    if data.get("error"):
        raise GetlawAPIError(f"getLaw API reported an error: {data!r}")

    content = data.get("content")
    if not content or not isinstance(content, str):
        raise GetlawAPIError("getLaw API response is missing a non-empty 'content' field")

    return content, _parse_lastupdate(data.get("lastupdate"))


# ---------------------------------------------------------------------------
# Cache-backed service
# ---------------------------------------------------------------------------


def _cache(conf: dict[str, Any]):
    return caches[conf["CACHE_ALIAS"]]


def _cache_key(text_type: str, api_key: str, conf: dict[str, Any]) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return f"{conf['CACHE_KEY_PREFIX']}{text_type}:{digest}"


def _store(
    text_type: str,
    api_key: str,
    content: str,
    conf: dict[str, Any],
    updated_at: int | None = None,
) -> None:
    """Persist a fresh fetch and clear any prior failure markers on the entry."""
    entry = {
        "content": content,
        "fetched_at": int(time.time()),
        "updated_at": updated_at,
        "api_version": conf["API_VERSION"],
        "last_error": None,
        "last_error_at": None,
    }
    # timeout=None: we manage freshness via fetched_at so we can serve stale
    # content as a fallback when the API is unreachable.
    _cache(conf).set(_cache_key(text_type, api_key, conf), entry, timeout=None)


def _record_failure(text_type: str, api_key: str, error_message: str, conf: dict[str, Any]) -> None:
    """Record the most recent fetch failure on the cache entry (creating one if needed).

    When there is no prior content, a marker entry with empty `content` is
    written so `fetch_failures()` can still report the situation.
    """
    entry = _load(text_type, api_key, conf) or {
        "content": "",
        "fetched_at": 0,
        "api_version": conf["API_VERSION"],
    }
    entry["last_error"] = error_message
    entry["last_error_at"] = int(time.time())
    _cache(conf).set(_cache_key(text_type, api_key, conf), entry, timeout=None)


def _load(text_type: str, api_key: str, conf: dict[str, Any]) -> dict[str, Any] | None:
    return _cache(conf).get(_cache_key(text_type, api_key, conf))


def get_text(text_type: str, *, force: bool = False) -> str:
    """Return the HTML for `text_type`, fetching from getLaw.de if needed.

    Lazy-refreshes the cache after `TTL_SECONDS`. When `force=True`, bypasses
    the freshness check and always calls the API. On API failure, falls back
    to the last known content for as long as it remains in the cache and
    records a failure marker so `GetlawAdminBannerMiddleware` can warn
    staff. Raises only when no cached content is available at all.

    Raises:
        GetlawConfigurationError: if `text_type` has no API key configured.
        GetlawAPIError: if the API call fails and no cached content exists.
    """
    cfg = _conf()
    api_key = _api_key_for(text_type, cfg)
    cached = _load(text_type, api_key, cfg)
    now = int(time.time())

    if (
        not force
        and cached
        and cached.get("content")
        and (now - int(cached.get("fetched_at", 0))) < cfg["TTL_SECONDS"]
    ):
        return cached["content"]

    try:
        content, updated_at = _fetch(api_key, conf=cfg)
    except GetlawAPIError as exc:
        _record_failure(text_type, api_key, str(exc), cfg)
        if cached and cached.get("content"):
            age = now - int(cached.get("fetched_at", 0))
            logger.warning(
                "django-getlaw: serving stale %r (age=%ss) after fetch failure",
                text_type,
                age,
            )
            return cached["content"]
        raise

    _store(text_type, api_key, content, cfg, updated_at)
    return content


def get_updated_at(text_type: str) -> datetime | None:
    """When getLaw last changed `text_type` (aware, UTC), for a "Stand" line.

    Read from the cache only, never from the API: the date arrives with each
    fetch. ``None`` when the text is not cached, was cached by 0.1.x, or
    getLaw sent no date.

    Raises:
        GetlawConfigurationError: if `text_type` has no API key configured.
    """
    cfg = _conf()
    entry = _load(text_type, _api_key_for(text_type, cfg), cfg) or {}
    updated_at = entry.get("updated_at")
    if not updated_at:
        return None
    return datetime.fromtimestamp(int(updated_at), tz=timezone.utc)


def refresh_text(text_type: str) -> str:
    """Force-refresh `text_type` from the API and return the new HTML."""
    return get_text(text_type, force=True)


def fetch_failures() -> list[dict[str, Any]]:
    """Return one row per configured text type whose most recent fetch failed.

    Each row contains: ``text_type``, ``last_error``, ``last_error_at``,
    ``fetched_at``, ``age_seconds`` (since last successful fetch, or ``None``
    if never), and ``has_content`` (True if a stale fallback is available).
    Rows are returned in the same order as :func:`configured_text_types`.
    """
    cfg = _conf()
    keys = cfg.get("KEYS") or {}
    now = int(time.time())
    rows: list[dict[str, Any]] = []
    for text_type in configured_text_types():
        api_key = str(keys[text_type])
        entry = _load(text_type, api_key, cfg)
        if not entry or not entry.get("last_error"):
            continue
        fetched_at = int(entry.get("fetched_at") or 0)
        rows.append(
            {
                "text_type": text_type,
                "last_error": entry["last_error"],
                "last_error_at": int(entry.get("last_error_at") or 0),
                "fetched_at": fetched_at,
                "age_seconds": (now - fetched_at) if fetched_at else None,
                "has_content": bool(entry.get("content")),
            }
        )
    return rows
