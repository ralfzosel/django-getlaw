"""django-getlaw: embed getLaw.de legal texts in Django projects."""

from django_getlaw.core import (
    GetlawAPIError,
    GetlawConfigurationError,
    GetlawError,
    fetch_failures,
    fetch_text,
    get_text,
    get_updated_at,
    refresh_text,
)

__version__ = "0.1.0"

__all__ = [
    "GetlawAPIError",
    "GetlawConfigurationError",
    "GetlawError",
    "__version__",
    "fetch_failures",
    "fetch_text",
    "get_text",
    "get_updated_at",
    "refresh_text",
]

default_app_config = "django_getlaw.apps.GetlawConfig"
