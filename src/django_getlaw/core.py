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
    "STALE_FALLBACK": False,
    "STALE_MAX_AGE_SECONDS": 7 * 86400,
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
            f"No API key configured for text type {text_type!r}. "
            f"Add it to settings.GETLAW['KEYS']."
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

    return content


# ---------------------------------------------------------------------------
# Cache-backed service
# ---------------------------------------------------------------------------


def _cache(conf: dict[str, Any]):
    return caches[conf["CACHE_ALIAS"]]


def _cache_key(text_type: str, api_key: str, conf: dict[str, Any]) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return f"{conf['CACHE_KEY_PREFIX']}{text_type}:{digest}"


def _store(text_type: str, api_key: str, content: str, conf: dict[str, Any]) -> None:
    entry = {
        "content": content,
        "fetched_at": int(time.time()),
        "api_version": conf["API_VERSION"],
    }
    # timeout=None: we manage freshness via fetched_at so we can serve stale
    # content as a fallback when the API is unreachable.
    _cache(conf).set(_cache_key(text_type, api_key, conf), entry, timeout=None)


def _load(text_type: str, api_key: str, conf: dict[str, Any]) -> dict[str, Any] | None:
    return _cache(conf).get(_cache_key(text_type, api_key, conf))


def get_text(text_type: str, *, force: bool = False) -> str:
    """Return the HTML for `text_type`, fetching from getLaw.de if needed.

    Lazy-refreshes the cache after `TTL_SECONDS`. When `force=True`, bypasses
    the freshness check and always calls the API. On API failure, falls back
    to the last known content if `STALE_FALLBACK` is enabled and the stale
    entry is younger than `STALE_MAX_AGE_SECONDS`.

    Raises:
        GetlawConfigurationError: if `text_type` has no API key configured.
        GetlawAPIError: if the API call fails and no usable fallback exists.
    """
    cfg = _conf()
    api_key = _api_key_for(text_type, cfg)
    cached = _load(text_type, api_key, cfg)
    now = int(time.time())

    if not force and cached and (now - int(cached.get("fetched_at", 0))) < cfg["TTL_SECONDS"]:
        return cached["content"]

    try:
        content = fetch_text(api_key, conf=cfg)
    except GetlawAPIError:
        if cfg["STALE_FALLBACK"] and cached:
            age = now - int(cached.get("fetched_at", 0))
            if age <= cfg["STALE_MAX_AGE_SECONDS"]:
                logger.warning(
                    "django-getlaw: serving stale %r (age=%ss) after fetch failure",
                    text_type,
                    age,
                )
                return cached["content"]
        raise

    _store(text_type, api_key, content, cfg)
    return content


def refresh_text(text_type: str) -> str:
    """Force-refresh `text_type` from the API and return the new HTML."""
    return get_text(text_type, force=True)
