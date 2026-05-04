"""Middleware that surfaces getLaw fetch failures to admin staff users.

Add ``django_getlaw.middleware.GetlawAdminBannerMiddleware`` after the
``django.contrib.messages`` middleware to get a persistent warning banner on
every Django admin page whenever one or more configured legal texts are
currently failing to refresh from the getLaw API.
"""

from __future__ import annotations

import logging

from django_getlaw.core import fetch_failures

logger = logging.getLogger("django_getlaw")


class GetlawAdminBannerMiddleware:
    """Add a ``messages.warning`` to admin pages while fetches are failing.

    The banner is re-added on every admin request and is cleared as soon as a
    subsequent fetch succeeds (which clears the per-text-type ``last_error``
    marker in the cache).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_staff", False):
            return None

        match = getattr(request, "resolver_match", None)
        if not match or "admin" not in getattr(match, "namespaces", []):
            return None

        try:
            failures = fetch_failures()
        except Exception:  # pragma: no cover - never break admin rendering
            logger.exception("django-getlaw: failed to evaluate fetch_failures()")
            return None

        if not failures:
            return None

        try:
            from django.contrib import messages
        except ImportError:  # pragma: no cover - django.contrib.messages required
            return None

        names = ", ".join(f["text_type"] for f in failures)
        last_error = failures[0]["last_error"]
        served_stale = any(f["has_content"] for f in failures)
        prefix = "serving stale content" if served_stale else "no content available"
        messages.warning(
            request,
            f"getLaw API: {prefix} for {names}. "
            f"Last error: {last_error}. "
            f"Fix the API key or connectivity, then run `python manage.py getlaw_refresh`.",
        )
        return None
