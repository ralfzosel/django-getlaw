"""Minimal Django settings for the django-getlaw test suite."""

DEBUG = False
SECRET_KEY = "test-key-not-for-production"  # noqa: S105

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "django_getlaw",
]

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "alt": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "alt",
    },
}

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django_getlaw.middleware.GetlawAdminBannerMiddleware",
]

ROOT_URLCONF = "tests.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "builtins": ["django_getlaw.templatetags.getlaw"],
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

GETLAW = {
    "KEYS": {
        "impressum": "test-impressum-key",
        "datenschutz": "test-datenschutz-key",
    },
}
