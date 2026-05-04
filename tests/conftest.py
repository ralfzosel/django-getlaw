"""Shared pytest fixtures for django-getlaw."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import caches


@pytest.fixture(autouse=True)
def _clear_caches():
    """Cache state must not leak between tests (LocMemCache is process-global)."""
    for alias in caches:
        caches[alias].clear()
    yield
    for alias in caches:
        caches[alias].clear()


class _FakeResponse:
    """Stand-in for the urlopen context-manager response object."""

    def __init__(self, body: bytes, charset: str = "utf-8"):
        self._body = body
        self._charset = charset

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    @property
    def headers(self):
        charset = self._charset

        class _Headers:
            def get_content_charset(self_inner):  # noqa: N805
                return charset

        return _Headers()


def _json_response(payload: dict[str, Any], charset: str = "utf-8") -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode(charset), charset=charset)


@pytest.fixture
def fake_api():
    """Patch the opener in core.fetch_text. Yields a controller object.

    Usage:
        def test_something(fake_api):
            fake_api.set_response({"error": False, "content": "<p>hi</p>"})
            ...
            assert fake_api.calls[0].full_url.endswith("/api/texts/abc")
    """

    class _Controller:
        def __init__(self):
            self.responses: list[Any] = []
            self.calls: list[Any] = []
            self._default = _json_response({"error": False, "content": "<p>OK</p>"})

        def set_response(
            self,
            payload: dict[str, Any] | None = None,
            *,
            raises: BaseException | None = None,
        ):
            if raises is not None:
                self.responses.append(raises)
            else:
                self.responses.append(
                    _json_response(payload or {"error": False, "content": "<p>OK</p>"})
                )

        def set_raw(self, body: bytes, *, charset: str = "utf-8"):
            self.responses.append(_FakeResponse(body, charset=charset))

        def _next(self, request, timeout=None):
            self.calls.append(request)
            response = self._default if not self.responses else self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    controller = _Controller()

    def _fake_build_opener(*handlers):
        class _Opener:
            def open(self_inner, request, timeout=None):  # noqa: N805
                return controller._next(request, timeout=timeout)

        return _Opener()

    with patch("django_getlaw.core.build_opener", _fake_build_opener):
        yield controller
