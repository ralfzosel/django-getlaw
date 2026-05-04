# Changelog

All notable changes to **django-getlaw** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- First release: `GETLAW` settings, `{% load getlaw %}{% getlaw "impressum" %}` tag, `get_text` / `refresh_text`, and `getlaw_refresh` management command; lazy cache with configurable TTL and key prefix.
- On upstream fetch failure, serve any cached HTML still in the cache (no maximum staleness), log a warning, and record `last_error` / `last_error_at` on the cache entry for observability.
- `fetch_failures()` — returns per-text-type rows (`text_type`, `last_error`, `last_error_at`, `fetched_at`, `age_seconds`, `has_content`) for custom dashboards or health checks.
- `GetlawAdminBannerMiddleware` (opt-in, after `MessageMiddleware`) — queues a `messages.warning` for staff on Django admin requests while any configured text has an outstanding fetch failure.
- System checks `django_getlaw.W001` and `django_getlaw.W002` when `GETLAW` still contains obsolete `STALE_FALLBACK` or `STALE_MAX_AGE_SECONDS` keys.
- Treat HTTP redirects from the API as failure (invalid key), matching getLaw’s behaviour.

### Changed

- Documentation: README describes stale fallback, admin middleware setup, and updated “How it works” flow.

### Removed

- `GETLAW["STALE_FALLBACK"]` and `GETLAW["STALE_MAX_AGE_SECONDS"]` — no longer read; remove from settings (system checks warn if still present).
