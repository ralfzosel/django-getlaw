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
    fetch_text,
    get_text,
    refresh_text,
)
from django_getlaw.core import _cache_key, _conf, configured_text_types

# ---------------------------------------------------------------------------
# Settings access
# ---------------------------------------------------------------------------


def test_conf_merges_defaults_with_user_settings():
    cfg = _conf()
    assert cfg["TTL_SECONDS"] == 86400
    assert cfg["KEYS"]["impressum"] == "test-impressum-key"
    assert cfg["USER_AGENT"].startswith("django-getlaw/")
    assert cfg["STALE_FALLBACK"] is False


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


def test_get_text_propagates_api_error_when_no_fallback(fake_api):
    fake_api.set_response(raises=URLError("boom"))
    with pytest.raises(GetlawAPIError):
        get_text("impressum")


@override_settings(
    GETLAW={
        "KEYS": {"impressum": "test-impressum-key"},
        "STALE_FALLBACK": True,
    }
)
def test_get_text_serves_stale_when_fallback_enabled(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    assert get_text("impressum") == "<p>v1</p>"

    # Make the entry stale and force a refresh attempt that fails.
    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    entry = cache.get(key)
    entry["fetched_at"] = int(time.time()) - 86400 - 1
    cache.set(key, entry, timeout=None)

    fake_api.set_response(raises=URLError("boom"))
    assert get_text("impressum") == "<p>v1</p>"


@override_settings(
    GETLAW={
        "KEYS": {"impressum": "test-impressum-key"},
        "STALE_FALLBACK": True,
        "STALE_MAX_AGE_SECONDS": 60,
    }
)
def test_stale_fallback_respects_max_age(fake_api):
    fake_api.set_response({"error": False, "content": "<p>v1</p>"})
    assert get_text("impressum") == "<p>v1</p>"

    cache = caches["default"]
    key = _cache_key("impressum", "test-impressum-key", _conf())
    entry = cache.get(key)
    entry["fetched_at"] = int(time.time()) - 999_999  # way past STALE_MAX_AGE_SECONDS
    cache.set(key, entry, timeout=None)

    fake_api.set_response(raises=URLError("boom"))
    with pytest.raises(GetlawAPIError):
        get_text("impressum")


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
