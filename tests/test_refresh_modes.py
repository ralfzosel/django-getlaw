"""``GETLAW["REFRESH"]``: lazy (default) versus scheduled refresh."""

from __future__ import annotations

import time

import pytest
from django.core.management import call_command
from django.template import Context, Template
from django.test import override_settings

from django_getlaw import GetlawConfigurationError, get_text, overdue_texts, refresh_text
from django_getlaw.apps import _check_refresh_mode
from django_getlaw.core import REFRESH_LAZY, REFRESH_SCHEDULED, _cache_key, _conf, _load
from tests.test_django_getlaw import _make_request, _run_middleware

KEYS = {"impressum": "test-impressum-key", "datenschutz": "test-datenschutz-key"}
SCHEDULED = {"KEYS": KEYS, "REFRESH": REFRESH_SCHEDULED}
LAZY = {"KEYS": KEYS, "REFRESH": REFRESH_LAZY}

DAY = 86400


def _seed(text_type: str, *, age: int, content: str = "<p>cached</p>") -> None:
    """A cache entry as a fetch ``age`` seconds ago would have written it."""
    from django.core.cache import caches

    cfg = _conf()
    caches["default"].set(
        _cache_key(text_type, cfg["KEYS"][text_type], cfg),
        {
            "content": content,
            "fetched_at": int(time.time()) - age,
            "updated_at": None,
            "api_version": "1",
            "last_error": None,
            "last_error_at": None,
        },
        timeout=None,
    )


def _fetched_at(text_type: str) -> int:
    cfg = _conf()
    return int(_load(text_type, cfg["KEYS"][text_type], cfg)["fetched_at"])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_lazy_is_the_default():
    assert _conf()["REFRESH"] == REFRESH_LAZY


@override_settings(GETLAW={"KEYS": KEYS, "REFRESH": "never"})
def test_unknown_refresh_mode_raises_configuration_error():
    with pytest.raises(GetlawConfigurationError) as excinfo:
        _conf()
    assert "'never'" in str(excinfo.value)


@override_settings(GETLAW={"KEYS": KEYS, "REFRESH": "never"})
def test_system_check_rejects_unknown_refresh_mode():
    errors = _check_refresh_mode(app_configs=None)
    assert [e.id for e in errors] == ["django_getlaw.E001"]
    assert "'lazy' or 'scheduled'" in errors[0].hint


@pytest.mark.parametrize("conf", [SCHEDULED, LAZY, {"KEYS": KEYS}])
def test_system_check_accepts_known_or_absent_refresh_mode(conf):
    with override_settings(GETLAW=conf):
        assert _check_refresh_mode(app_configs=None) == []


# ---------------------------------------------------------------------------
# get_text in scheduled mode
# ---------------------------------------------------------------------------


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_serves_an_expired_text_without_fetching(fake_api):
    _seed("impressum", age=2 * DAY, content="<p>two days old</p>")

    assert get_text("impressum") == "<p>two days old</p>"
    assert fake_api.calls == []


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_serves_an_arbitrarily_old_text_without_fetching(fake_api):
    _seed("impressum", age=5 * 365 * DAY, content="<p>ancient</p>")

    assert get_text("impressum") == "<p>ancient</p>"
    assert fake_api.calls == []


@override_settings(GETLAW=LAZY)
def test_lazy_mode_still_fetches_an_expired_text(fake_api):
    _seed("impressum", age=2 * DAY, content="<p>two days old</p>")
    fake_api.set_response({"error": False, "content": "<p>fresh</p>"})

    assert get_text("impressum") == "<p>fresh</p>"
    assert len(fake_api.calls) == 1


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_fetches_when_nothing_is_cached(fake_api):
    """An empty imprint is worse than one slow page: a first fetch still happens."""
    fake_api.set_response({"error": False, "content": "<p>first</p>"})

    assert get_text("impressum") == "<p>first</p>"
    assert len(fake_api.calls) == 1
    assert get_text("impressum") == "<p>first</p>"
    assert len(fake_api.calls) == 1


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_fetches_when_only_a_failure_marker_is_cached(fake_api):
    from django.core.cache import caches

    cfg = _conf()
    caches["default"].set(
        _cache_key("impressum", cfg["KEYS"]["impressum"], cfg),
        {"content": "", "fetched_at": 0, "api_version": "1", "last_error": "HTTP 503"},
        timeout=None,
    )
    fake_api.set_response({"error": False, "content": "<p>first</p>"})

    assert get_text("impressum") == "<p>first</p>"
    assert len(fake_api.calls) == 1


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_force_and_refresh_text_fetch(fake_api):
    _seed("impressum", age=2 * DAY, content="<p>old</p>")
    fake_api.set_response({"error": False, "content": "<p>forced</p>"})
    fake_api.set_response({"error": False, "content": "<p>refreshed</p>"})

    assert get_text("impressum", force=True) == "<p>forced</p>"
    assert refresh_text("impressum") == "<p>refreshed</p>"
    assert len(fake_api.calls) == 2
    assert _fetched_at("impressum") >= int(time.time()) - 5


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_command_renews_the_texts(fake_api):
    _seed("impressum", age=2 * DAY, content="<p>old imp</p>")
    _seed("datenschutz", age=2 * DAY, content="<p>old dat</p>")
    fake_api.set_response({"error": False, "content": "<p>new dat</p>"})
    fake_api.set_response({"error": False, "content": "<p>new imp</p>"})

    call_command("getlaw_refresh")

    assert len(fake_api.calls) == 2
    assert get_text("datenschutz") == "<p>new dat</p>"
    assert get_text("impressum") == "<p>new imp</p>"
    assert len(fake_api.calls) == 2


@override_settings(GETLAW=SCHEDULED)
def test_scheduled_mode_template_tag_renders_an_expired_text_without_fetching(fake_api):
    _seed("datenschutz", age=3 * DAY, content="<p>privacy</p>")

    rendered = Template('{% load getlaw %}{% getlaw "datenschutz" %}').render(Context())

    assert rendered == "<p>privacy</p>"
    assert fake_api.calls == []


# ---------------------------------------------------------------------------
# overdue_texts
# ---------------------------------------------------------------------------


def test_overdue_texts_is_empty_while_every_text_is_within_ttl():
    _seed("impressum", age=DAY - 60)
    _seed("datenschutz", age=60)

    assert overdue_texts() == []


def test_overdue_texts_lists_expired_and_never_fetched_texts():
    _seed("impressum", age=2 * DAY)

    rows = overdue_texts()

    assert [row["text_type"] for row in rows] == ["datenschutz", "impressum"]
    datenschutz, impressum = rows
    assert datenschutz == {
        "text_type": "datenschutz",
        "fetched_at": 0,
        "age_seconds": None,
        "has_content": False,
    }
    assert impressum["has_content"] is True
    assert 2 * DAY <= impressum["age_seconds"] <= 2 * DAY + 5


# ---------------------------------------------------------------------------
# Admin banner
# ---------------------------------------------------------------------------


@override_settings(GETLAW=SCHEDULED)
def test_middleware_warns_about_overdue_texts_in_scheduled_mode():
    _seed("impressum", age=3 * DAY)
    _seed("datenschutz", age=60)

    captured = _run_middleware(_make_request(is_staff=True, namespaces=["admin"]))

    assert len(captured) == 1
    message = captured[0][1]
    assert "impressum" in message
    assert "datenschutz" not in message
    assert "getlaw_refresh" in message


@override_settings(GETLAW=LAZY)
def test_middleware_ignores_overdue_texts_in_lazy_mode():
    _seed("impressum", age=3 * DAY)

    assert _run_middleware(_make_request(is_staff=True, namespaces=["admin"])) == []


@override_settings(GETLAW=SCHEDULED)
def test_middleware_silent_in_scheduled_mode_while_texts_are_current():
    _seed("impressum", age=60)
    _seed("datenschutz", age=60)

    assert _run_middleware(_make_request(is_staff=True, namespaces=["admin"])) == []
