# django-getlaw

Embed legal texts (Impressum, Datenschutzerklärung, AGB, Widerrufsbelehrung,
Barrierefreiheitserklärung) from [getLaw.de](https://www.getlaw.de) in your
Django project.

This is the Django counterpart to the official getLaw plugins for
[WordPress](https://www.getlaw.de/api/wordpress/) and
[Contao](https://www.getlaw.de/api/contao/). It calls the same public
[getLaw API](https://www.getlaw.de/api/) (`/api/texts/{api_key}` with the
`X-getLaw-API-Version: 1` header), caches the response in your Django cache
backend, and refreshes it lazily every 24 hours (configurable). A management
command is provided for cron- or `django-q`-driven warming.

The package depends on **Django only** (no third-party HTTP client) and uses
the standard library `urllib` so it works wherever Django works.

## Installation

```bash
uv add django-getlaw          # or: pip install django-getlaw
```

Add the app to `INSTALLED_APPS`:

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "django_getlaw",
]
```

## Configuration

All settings live under a single `GETLAW` dict. Only `KEYS` is required; every
text type you want to render must have an API key here. Get the keys from your
getLaw.de account (one key per text).

```python
# settings.py
GETLAW = {
    "KEYS": {
        "impressum": os.environ["GETLAW_KEY_IMPRESSUM"],
        "datenschutz": os.environ["GETLAW_KEY_DATENSCHUTZ"],
        # "agb": "...",
        # "widerruf": "...",
        # "barrierefreiheit": "...",
    },

    # Optional, shown with their defaults:
    "TTL_SECONDS": 86400,                              # 24 hours
    "API_BASE_URL": "https://www.getlaw.de/api/texts/",
    "API_VERSION": "1",
    "TIMEOUT_SECONDS": 10,
    "CACHE_ALIAS": "default",                          # any django CACHES alias
    "CACHE_KEY_PREFIX": "getlaw:",
    "USER_AGENT": "django-getlaw/<version>",           # auto-filled with package version
}
```

> Use a **shared** cache backend (Redis, Memcached, or Django's
> `DatabaseCache`) in multi-worker deployments. With `LocMemCache`, every
> worker maintains its own cache and will fetch independently.

> The HTTP client honours the standard `HTTP_PROXY` / `HTTPS_PROXY`
> environment variables automatically.

## Usage

### Template tag

```django
{% load getlaw %}

<section>
  {% getlaw "impressum" %}
</section>
```

The tag returns safe HTML. On `GETLAW` misconfiguration or fetch failure it
returns an empty string in production (`DEBUG=False`) and a visible HTML
comment when `DEBUG=True`, so problems are obvious in development without
breaking pages in production.

### Programmatic API

```python
from django_getlaw import get_text, refresh_text

html = get_text("impressum")             # cached, lazy refresh after TTL
html = get_text("impressum", force=True) # bypass TTL, fetch now
html = refresh_text("impressum")         # alias for force-fetch
```

### Management command

```bash
# Refresh every configured text:
uv run python manage.py getlaw_refresh

# Refresh specific texts only:
uv run python manage.py getlaw_refresh impressum datenschutz
```

The command exits with a non-zero status if any refresh fails — wire it into
cron or your scheduler so failures alert you.

### Admin banner on fetch failures

`django-getlaw` always falls back to the last known content when an API
fetch fails — there is no upper bound on staleness, because a slightly
outdated Impressum is virtually always better than a blank page. To make
sure the failure doesn't go unnoticed, add the bundled middleware after
Django's messages middleware:

```python
# settings.py
MIDDLEWARE = [
    # ...
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django_getlaw.middleware.GetlawAdminBannerMiddleware",
]
```

Any staff user (`request.user.is_staff`) visiting a Django admin page will
see a warning banner listing the affected text types and the most recent
error. The banner re-renders on every admin request and disappears as soon
as a successful fetch (template tag, `refresh_text(...)`, or
`getlaw_refresh`) clears the failure marker.

If you need the failure list programmatically (e.g. for a status endpoint
or your own dashboard), call `django_getlaw.fetch_failures()`.

#### Scheduling with django-q

```python
from django_q.tasks import schedule
from django_q.models import Schedule

schedule(
    "django.core.management.call_command",
    "getlaw_refresh",
    name="getlaw-refresh-daily",
    schedule_type=Schedule.DAILY,
)
```

## Supported text types

`impressum`, `datenschutz`, `agb`, `widerruf`, `barrierefreiheit` — but the
package does **not** hard-code that list. Any text type for which you put a
key in `GETLAW["KEYS"]` is fetchable; the API decides what's valid. This
means you don't have to wait for a package release if getLaw adds a new text
type in the future.

## How it works

1. The template tag / `get_text(...)` looks up the API key by text type in
   `GETLAW["KEYS"]`.
2. It checks the configured Django cache for a fresh entry (newer than
   `TTL_SECONDS`). If fresh, the cached HTML is returned.
3. Otherwise the package calls
   `GET https://www.getlaw.de/api/texts/{api_key}` with the
   `X-getLaw-API-Version: 1` header, parses the JSON response, and caches the
   `content` field.
4. On HTTP redirects (the way the getLaw API signals "invalid key"), HTTP
   errors, timeouts, or unparseable responses, the call raises a
   `GetlawAPIError`. If any cached content exists, it is returned as a
   fallback (regardless of age), a warning is logged, and a per-text-type
   failure marker is recorded so `GetlawAdminBannerMiddleware` can warn
   staff on the next admin page. The marker clears the next time a fetch
   succeeds. If nothing has ever been fetched successfully, the error
   propagates and the template tag renders empty (or an HTML comment in
   `DEBUG`).

The cache key includes a hash of the API key, so rotating a key in your
settings transparently invalidates the corresponding entry.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

## Licence

MIT — see [LICENSE](LICENSE). The upstream WordPress and Contao plugins by
getLaw.de are GPL-3.0; this package contains no code from them — it was
written from the public API contract.

## Acknowledgements

Thanks to [getLaw.de](https://www.getlaw.de) for providing a clean public
API.
