from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Warning, register


class GetlawConfig(AppConfig):
    name = "django_getlaw"
    verbose_name = "getLaw"

    def ready(self):
        register(_check_obsolete_settings)


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
