"""Tests for django-getlaw."""

from __future__ import annotations

import time
from urllib.error import HTTPError, URLError

import pytest
from django.core.cache import caches
from django.core.management import CommandError, call_command
from django.template import Context, Template
from django.test import override_settings

from django_getlaw import (
    GetlawAPIError,
    GetlawConfigurationError,
    fetch_failures,
    fetch_text,
    get_text,
    refresh_text,
)
from django_getlaw.core import _cache_key, _conf, configured_text_types
from django_getlaw.middleware import GetlawAdminBannerMiddleware

# ---------------------------------------------------------------------------
# Settings access
# ---------------------------------------------------------------------------


def test_conf_merges_defaults_with_user_settings():
    cfg = _conf()
    assert cfg["TTL_SECONDS"] == 86400
    assert cfg["KEYS"]["impressum"] == "test-impressum-key"
    assert cfg["USER_AGENT"].startswith("django-getlaw/")
    assert "STALE_FALLBACK" not in cfg
    assert "STALE_MAX_AGE_SECONDS" not in cfg


def test_configured_text_types_returns_only_keys_with_values():
    assert configured_text_types() == ["datenschutz", "impressum"]


@override_settings(GETLAW="not a dict")
def test_conf_rejects_non_dict_settings():
    with pytest.raises(GetlawConfigurationError):
        _conf()


# ---------------------------------------------------------------------------
# fetch_text — HTTP client
# ---------------------------------------------------------------------------


def test_fetch_text_builds_correct_request(fake_api):
    fake_api.set_response({"error": False, "content": "<p>hi</p>"})

    content = fetch_text("test-impressum-key")

    assert content == "<p>hi</p>"
    request = fake_api.calls[0]
    assert request.full_url == "https://www.getlaw.de/api/texts/test-impressum-key"
    assert request.headers["X-getlaw-api-version"] == "1"
    assert request.headers["User-agent"].startswith("django-getlaw/")
    assert request.get_method() == "GET"


def test_fetch_text_raises_on_redirect(fake_api):
    fake_api.set_response(
        raises=HTTPError("https://www.getlaw.de/api/texts/x", 303, "See Other", {}, None)
    )

    with pytest.raises(GetlawAPIError) as excinfo:
        fetch_text("test-impressum-key")
    assert "303" in str(excinfo.value)


def test_fetch_text_raises_on_url_error(fake_api):
    fake_api.set_response(raises=URLError("connection refused"))

    with pytest.raises(GetlawAPIError):
        fetch_text("test-impressum-key")


def test_fetch_text_raises_on_timeout(fake_api):
    fake_api.set_response(raises=TimeoutError("read timed out"))

    with pytest.raises(GetlawAPIError):
        fetch_text("test-impressum-key")


def test_fetch_text_raises_on_non_json(fake_api):
    fake_api.set_raw(b"<html>not json</html>")

    with pytest.raises(GetlawAPIError) as excinfo:
        fetch_text("test-impressum-key")
    assert "non-JSON" in str(excinfo.value)


def test_fetch_text_raises_when_payload_signals_error(fake_api):
    fake_api.set_response({"error": True, "message": "invalid key"})

    with pytest.raises(GetlawAPIError):
        fetch_text("test-impressum-key")


def test_fetch_text_raises_when_content_missing(fake_api):
    fake_api.set_response({"error": False})

    with pytest.raises(GetlawAPIError):
        fetch_text("test-impressum-key")


def test_fetch_text_rejects_empty_api_key():
    with pytest.raises(GetlawConfigurationError):
        fetch_text("")


# ---------------------------------------------------------------------------
# get_text / refresh_text — cache + service
# ---------------------------------------------------------------------------


def test_get_text_caches_after_first_fetch(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})

    assert get_text("impressum") == "<p>v1</p>"
    assert get_text("impressum") == "<p>v1</p>"  # served from cache
    assert len(fake_api.calls) == 1


def test_get_text_refreshes_after_ttl(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    fake_api.set_response({"error": False, "content": "<p>v2</p>"})

    assert get_text("impressum") == "<p>v1</p>"

    # Move the cached fetched_at into the past beyond TTL.
    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    entry = cache.get(key)
    entry["fetched_at"] = int(time.time()) - 86400 - 1
    cache.set(key, entry, timeout=None)

    assert get_text("impressum") == "<p>v2</p>"
    assert len(fake_api.calls) == 2


def test_force_bypasses_cache(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    fake_api.set_response({"error": False, "content": "<p>v2</p>"})

    assert get_text("impressum") == "<p>v1</p>"
    assert refresh_text("impressum") == "<p>v2</p>"
    assert len(fake_api.calls) == 2


def test_get_text_unknown_type_raises_configuration_error():
    with pytest.raises(GetlawConfigurationError):
        get_text("nonexistent-type")


def test_get_text_propagates_api_error_when_no_cached_content(fake_api):
    fake_api.set_response(raises=URLError("boom"))
    with pytest.raises(GetlawAPIError):
        get_text("impressum")


def test_get_text_serves_stale_by_default(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    assert get_text("impressum") == "<p>v1</p>"

    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    entry = cache.get(key)
    entry["fetched_at"] = int(time.time()) - 86400 - 1
    cache.set(key, entry, timeout=None)

    fake_api.set_response(raises=URLError("boom"))
    assert get_text("impressum") == "<p>v1</p>"


def test_get_text_serves_arbitrarily_old_stale_content(fake_api):
    """No upper bound on stale age \u2014 ancient cached content is still served."""
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    assert get_text("impressum") == "<p>v1</p>"

    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    entry = cache.get(key)
    entry["fetched_at"] = int(time.time()) - 365 * 86400 * 5  # 5 years old
    cache.set(key, entry, timeout=None)

    fake_api.set_response(raises=URLError("boom"))
    assert get_text("impressum") == "<p>v1</p>"


def test_cache_key_changes_when_api_key_rotates():
    cfg = _conf()
    a = _cache_key("impressum", "key-a", cfg)
    b = _cache_key("impressum", "key-b", cfg)
    assert a != b


# ---------------------------------------------------------------------------
# Template tag
# ---------------------------------------------------------------------------


def test_template_tag_renders_safe_html(fake_api):
    fake_api.set_response({"error": False, "content": "<p>tag</p>"})
    rendered = Template('{% load getlaw %}{% getlaw "impressum" %}').render(Context())
    assert rendered == "<p>tag</p>"


def test_template_tag_swallows_errors_in_production(fake_api):
    fake_api.set_response(raises=URLError("boom"))
    with override_settings(DEBUG=False):
        rendered = Template('{% load getlaw %}{% getlaw "impressum" %}').render(Context())
    assert rendered == ""


def test_template_tag_emits_visible_comment_in_debug(fake_api):
    fake_api.set_response(raises=URLError("boom"))
    with override_settings(DEBUG=True):
        rendered = Template('{% load getlaw %}{% getlaw "impressum" %}').render(Context())
    assert rendered.startswith("<!-- django-getlaw error for impressum:")


def test_template_tag_unknown_type_does_not_break_rendering():
    with override_settings(DEBUG=False):
        rendered = Template('{% load getlaw %}{% getlaw "nope" %}').render(Context())
    assert rendered == ""


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


def test_command_refreshes_all_configured_keys(fake_api, capsys):
    fake_api.set_response({"error": False, "content": "<p>imp</p>"})
    fake_api.set_response({"error": False, "content": "<p>dat</p>"})

    call_command("getlaw_refresh")

    captured = capsys.readouterr()
    assert "OK    impressum" in captured.out
    assert "OK    datenschutz" in captured.out
    assert len(fake_api.calls) == 2


def test_command_refreshes_only_specified_types(fake_api, capsys):
    fake_api.set_response({"error": False, "content": "<p>imp</p>"})

    call_command("getlaw_refresh", "impressum")

    captured = capsys.readouterr()
    assert "OK    impressum" in captured.out
    assert "datenschutz" not in captured.out
    assert len(fake_api.calls) == 1


def test_command_exits_nonzero_on_failure(fake_api, capsys):
    fake_api.set_response(raises=URLError("boom"))

    with pytest.raises(CommandError):
        call_command("getlaw_refresh", "impressum")

    captured = capsys.readouterr()
    assert "FAIL  impressum" in captured.err


@override_settings(GETLAW={"KEYS": {}})
def test_command_errors_when_nothing_to_refresh():
    with pytest.raises(CommandError):
        call_command("getlaw_refresh")


# ---------------------------------------------------------------------------
# fetch_failures
# ---------------------------------------------------------------------------


def test_fetch_failures_empty_when_nothing_failed():
    assert fetch_failures() == []


def test_fetch_failures_records_failure_and_clears_on_success(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    get_text("impressum")

    # Force a refresh that fails: stale fallback returns v1, but the failure
    # marker is recorded on the entry.
    fake_api.set_response(raises=URLError("boom"))
    assert get_text("impressum", force=True) == "<p>v1</p>"

    failures = fetch_failures()
    types = {row["text_type"] for row in failures}
    assert "impressum" in types
    impressum_row = next(r for r in failures if r["text_type"] == "impressum")
    assert impressum_row["has_content"] is True
    assert "boom" in impressum_row["last_error"]

    fake_api.set_response({"error": False, "content": "<p>v2</p>"})
    refresh_text("impressum")
    assert all(r["text_type"] != "impressum" for r in fetch_failures())


def test_fetch_failures_marks_text_with_no_content(fake_api):
    fake_api.set_response(raises=URLError("boom"))
    with pytest.raises(GetlawAPIError):
        get_text("impressum")

    failures = fetch_failures()
    impressum_row = next(r for r in failures if r["text_type"] == "impressum")
    assert impressum_row["has_content"] is False
    assert impressum_row["age_seconds"] is None


# ---------------------------------------------------------------------------
# Admin banner middleware
# ---------------------------------------------------------------------------


def _seed_failure(text_type: str = "impressum", api_key: str = "test-impressum-key") -> None:
    """Write a failure marker straight into the cache (cheap test fixture)."""
    cache = caches["default"]
    key = _cache_key(text_type, api_key, _conf())
    cache.set(
        key,
        {
            "content": "<p>stale</p>",
            "fetched_at": int(time.time()) - 100,
            "api_version": "1",
            "last_error": "boom",
            "last_error_at": int(time.time()),
        },
        timeout=None,
    )


class _Match:
    def __init__(self, namespaces):
        self.namespaces = namespaces


def _make_request(*, is_staff: bool, namespaces: list[str], anonymous: bool = False):
    """Build a real Django request with messages storage attached."""
    from django.contrib.auth.models import AnonymousUser, User
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    request = RequestFactory().get("/admin/")
    request.session = {}
    request._messages = FallbackStorage(request)
    if anonymous:
        request.user = AnonymousUser()
    else:
        request.user = User(username="tester", is_staff=is_staff)
    request.resolver_match = _Match(namespaces)
    return request


def _captured_messages(request) -> list[tuple[int, str]]:
    return [(m.level, m.message) for m in request._messages]


def _run_middleware(request) -> list[tuple[int, str]]:
    middleware = GetlawAdminBannerMiddleware(lambda r: None)
    middleware.process_view(request, lambda r: None, (), {})
    return _captured_messages(request)


def test_middleware_adds_warning_for_staff_on_admin_url():
    _seed_failure()
    request = _make_request(is_staff=True, namespaces=["admin"])
    captured = _run_middleware(request)

    from django.contrib.messages import constants as message_constants

    assert len(captured) == 1
    level, message = captured[0]
    assert level == message_constants.WARNING
    assert "impressum" in message
    assert "serving stale content" in message


def test_middleware_silent_when_no_failures():
    request = _make_request(is_staff=True, namespaces=["admin"])
    assert _run_middleware(request) == []


def test_middleware_silent_for_anonymous_user():
    _seed_failure()
    request = _make_request(is_staff=False, namespaces=["admin"], anonymous=True)
    assert _run_middleware(request) == []


def test_middleware_silent_for_non_staff_user():
    _seed_failure()
    request = _make_request(is_staff=False, namespaces=["admin"])
    assert _run_middleware(request) == []


def test_middleware_silent_outside_admin():
    _seed_failure()
    request = _make_request(is_staff=True, namespaces=[])
    assert _run_middleware(request) == []


def test_middleware_warning_mentions_no_content_state():
    """When the only failures have no cached content, message reflects that."""
    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    cache.set(
        key,
        {
            "content": "",
            "fetched_at": 0,
            "api_version": "1",
            "last_error": "HTTP 503",
            "last_error_at": int(time.time()),
        },
        timeout=None,
    )
    request = _make_request(is_staff=True, namespaces=["admin"])
    captured = _run_middleware(request)
    assert len(captured) == 1
    assert "no content available" in captured[0][1]
    assert "HTTP 503" in captured[0][1]


# ---------------------------------------------------------------------------
# Obsolete settings system check
# ---------------------------------------------------------------------------


@override_settings(
    GETLAW={
        "KEYS": {"impressum": "test-impressum-key"},
        "STALE_FALLBACK": True,
    }
)
def test_system_check_warns_when_stale_fallback_still_set():
    from django_getlaw.apps import _check_obsolete_settings

    warnings = _check_obsolete_settings(app_configs=None)
    assert [w.id for w in warnings] == ["django_getlaw.W001"]


@override_settings(
    GETLAW={
        "KEYS": {"impressum": "test-impressum-key"},
        "STALE_MAX_AGE_SECONDS": 3600,
    }
)
def test_system_check_warns_when_stale_max_age_still_set():
    from django_getlaw.apps import _check_obsolete_settings

    warnings = _check_obsolete_settings(app_configs=None)
    assert [w.id for w in warnings] == ["django_getlaw.W002"]


@override_settings(
    GETLAW={
        "KEYS": {"impressum": "test-impressum-key"},
        "STALE_FALLBACK": True,
        "STALE_MAX_AGE_SECONDS": 3600,
    }
)
def test_system_check_warns_for_each_obsolete_key():
    from django_getlaw.apps import _check_obsolete_settings

    ids = sorted(w.id for w in _check_obsolete_settings(app_configs=None))
    assert ids == ["django_getlaw.W001", "django_getlaw.W002"]


def test_system_check_silent_when_no_obsolete_keys():
    from django_getlaw.apps import _check_obsolete_settings

    assert _check_obsolete_settings(app_configs=None) == []
