# django-getlaw

Embed legal texts (Impressum, Datenschutzerklärung, AGB, Widerrufsbelehrung,
Barrierefreiheitserklärung) from [getLaw.de](https://www.getlaw.de) in your
Django project.

This is the Django counterpart to the official getLaw plugins for
[WordPress](https://www.getlaw.de/api/wordpress/) and
[Contao](https://www.getlaw.de/api/contao/). It calls the same
[getLaw client API](https://www.getlaw.de/api/) (`/api/texts/{api_key}` with the
`X-getLaw-API-Version: 1` header), caches the response in your Django cache
backend, and refreshes it every 24 hours (configurable). By default the
refresh is lazy: the next page render after `TTL_SECONDS` fetches fresh HTML,
so you do **not** need a scheduler for correctness. Where a page render must
never wait on getLaw.de, set `REFRESH` to `"scheduled"` and let the bundled
management command do every fetch—see
[Refresh modes](#refresh-modes) and
[Scheduling `getlaw_refresh`](#scheduling-getlaw_refresh).

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
    "REFRESH": "lazy",                                 # or "scheduled", see Refresh modes
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

### "Stand" date

getLaw sends with each text when it last changed it (`lastupdate`), and
django-getlaw keeps that date with the cached text. For a "Stand" line:

```django
{% load getlaw %}
{% getlaw "datenschutz" %}
{% getlaw_updated_at "datenschutz" as stand %}
{% if stand %}<p>Stand {{ stand|date:"d.m.Y" }}</p>{% endif %}
```

The tag gives an aware datetime, or an empty string when the date is unknown,
so the line disappears instead of showing a wrong date. Texts cached by 0.1.x
carry no date until their next fetch; `manage.py getlaw_refresh` fills it at
once. The fetch time is no substitute: the text is fetched again every
`TTL_SECONDS`, so that date moves forward every day.

### Programmatic API

```python
from django_getlaw import get_text, get_updated_at, refresh_text

html = get_text("impressum")             # cached, lazy refresh after TTL
html = get_text("impressum", force=True) # bypass TTL, fetch now
html = refresh_text("impressum")         # alias for force-fetch
when = get_updated_at("datenschutz")     # when getLaw last changed it, or None
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

### Refresh modes

`GETLAW["REFRESH"]` decides who fetches a text that is older than
`TTL_SECONDS`:

- **`"lazy"`** (default) — the first page render after `TTL_SECONDS` fetches
  the text while the visitor waits (`TIMEOUT_SECONDS`, 10 s by default). If
  that fetch fails, the stale text is served, and **every** following render
  fetches again until one succeeds: during a getLaw.de outage each view of the
  imprint page holds a worker for up to the timeout.
- **`"scheduled"`** — a render serves the cached text whatever its age and
  never calls getLaw.de; only `manage.py getlaw_refresh` and
  `refresh_text()` / `get_text(..., force=True)` do. Use it where no public
  view may call out, and schedule `getlaw_refresh` at least once per
  `TTL_SECONDS` (below). A render still fetches when **nothing** is cached
  (first deploy, cleared cache): an empty imprint is worse than one slow page.
  Since nothing else would show a schedule that stopped running,
  `GetlawAdminBannerMiddleware` warns staff about texts older than
  `TTL_SECONDS` in this mode, and `overdue_texts()` lists them for your own
  dashboard.

An unknown value fails `manage.py check` (`django_getlaw.E001`).

### Scheduling `getlaw_refresh`

Lazy refresh is enough if traffic regularly hits every embedded text before the
cache goes stale. A **scheduled** run is still recommended, and with
`REFRESH = "scheduled"` it is the only refresh there is, because:

- **Latency** — After `TTL_SECONDS`, the first visitor pays the HTTP round-trip
  to getLaw.de; a scheduled job refreshes the cache in the background so normal
  page loads stay fast.
- **Low-traffic pages** — Legal snippets on rarely opened URLs might otherwise
  stay cached longer than you expect until someone loads that page.
- **Failures you can alert on** — `getlaw_refresh` exits non-zero when a
  refresh fails, so cron or your job runner can notify you. Lazy refresh alone
  only triggers fetches when a page is rendered (you still get the admin
  banner for staff if you enabled it, but no automatic ops alert unless you add
  one).

Pick one approach below.

#### Cron (system scheduler)

Run daily (or as often as you like) under the same Unix user and environment
as the app. Example: every day at 03:00, log stdout/stderr:

```bash
0 3 * * * cd /path/to/your/project && /path/to/.venv/bin/python manage.py getlaw_refresh >> /var/log/getlaw_refresh.log 2>&1
```

Adjust paths; use `uv run python manage.py getlaw_refresh` if that is how you
invoke Django in production. Point `$MAILTO` or your monitoring at non-zero
exit codes from the cron job if you need alerts.

#### Django-Q (admin or code)

[Django-Q](https://github.com/Koed00/django-q) can run the same command on a
schedule. The **`qcluster`** worker process must be running continuously;
otherwise queued schedules never execute.

**Admin UI:** Django admin → **Django Q** → **Scheduled tasks** → **Add**.

| Field | Value |
| --- | --- |
| **Func** | `django.core.management.call_command` |
| **Args** | `getlaw_refresh` — or `getlaw_refresh,impressum,datenschutz` to refresh only those types (comma-separated, same as CLI arguments after the command name). |
| **Schedule type** | **Daily** — repeats every day at the **clock time** taken from **Next run** (the first run sets that time). Use **Weekly** / **Cron** if you prefer. |
| **Next run** | Next datetime the job should run (project timezone). |
| **Repeats** | `-1` for “forever”. |

Leave **Cluster** blank unless you use [named clusters](https://django-q2.readthedocs.io/en/latest/configure.html).

**Code:**

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
   `TTL_SECONDS`). If fresh, the cached HTML is returned. With
   `REFRESH = "scheduled"`, any cached HTML is returned, however old; only
   `getlaw_refresh` / `refresh_text()` go on to step 3.
3. Otherwise the package calls
   `GET https://www.getlaw.de/api/texts/{api_key}` with the
   `X-getLaw-API-Version: 1` header, parses the JSON response, and caches the
   `content` field together with `lastupdate` (when getLaw last changed the
   text).
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
getLaw.de are GPL-3.0; this package contains no code from them — it simply
calls the same client HTTP API (endpoint and headers) as those plugins. The
[getLaw API page](https://www.getlaw.de/api/) is only a short overview.

## Acknowledgements

Thanks to [getLaw.de](https://www.getlaw.de) for providing a clean API for
clients (paid subscription; requires an API key).
