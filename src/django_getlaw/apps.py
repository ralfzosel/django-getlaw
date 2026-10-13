from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, Warning, register


class GetlawConfig(AppConfig):
    name = "django_getlaw"
    verbose_name = "getLaw"

    def ready(self):
        register(_check_obsolete_settings)
        register(_check_refresh_mode)


_OBSOLETE_KEYS = {
    "STALE_FALLBACK": (
        "django_getlaw.W001",
        "settings.GETLAW['STALE_FALLBACK'] is no longer used.",
        "Stale fallback is now always on. Remove the STALE_FALLBACK key from settings.GETLAW.",
    ),
    "STALE_MAX_AGE_SECONDS": (
        "django_getlaw.W002",
        "settings.GETLAW['STALE_MAX_AGE_SECONDS'] is no longer used.",
        (
            "Cached content is now served as a fallback for as long as it remains in the "
            "cache. Remove the STALE_MAX_AGE_SECONDS key from settings.GETLAW."
        ),
    ),
}


def _check_obsolete_settings(app_configs, **kwargs):
    user = getattr(settings, "GETLAW", None)
    if not isinstance(user, dict):
        return []
    return [
        Warning(message, hint=hint, id=check_id)
        for key, (check_id, message, hint) in _OBSOLETE_KEYS.items()
        if key in user
    ]


def _check_refresh_mode(app_configs, **kwargs):
    """An unknown ``REFRESH`` value would blank every text at render time."""
    from django_getlaw.core import REFRESH_MODES

    user = getattr(settings, "GETLAW", None)
    if not isinstance(user, dict) or "REFRESH" not in user or user["REFRESH"] in REFRESH_MODES:
        return []
    return [
        Error(
            f"settings.GETLAW['REFRESH'] is {user['REFRESH']!r}.",
            hint="Use " + " or ".join(repr(mode) for mode in REFRESH_MODES) + ".",
            id="django_getlaw.E001",
        )
    ]
