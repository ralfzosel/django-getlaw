"""Template tag: ``{% load getlaw %}{% getlaw "impressum" %}``."""

from __future__ import annotations

import logging

from django import template
from django.conf import settings
from django.utils.html import escape
from django.utils.safestring import mark_safe

from django_getlaw.core import GetlawError, get_text

register = template.Library()
logger = logging.getLogger("django_getlaw")


@register.simple_tag(name="getlaw")
def getlaw_tag(text_type: str, *, force: bool = False) -> str:
    """Render a getLaw legal text as safe HTML.

    On configuration or API errors the tag never raises during rendering.
    Instead it returns an empty string in production and a visible HTML
    comment when ``DEBUG`` is true, so problems show up early in development
    without breaking pages in production.
    """
    try:
        return mark_safe(get_text(text_type, force=force))  # noqa: S308 - upstream HTML
    except GetlawError as exc:
        logger.error("django-getlaw: failed to render %r: %s", text_type, exc)
        if getattr(settings, "DEBUG", False):
            return mark_safe(  # noqa: S308 - escaped string only
                f"<!-- django-getlaw error for {escape(text_type)}: {escape(str(exc))} -->"
            )
        return ""
