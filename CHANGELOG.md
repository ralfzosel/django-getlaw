# Changelog

All notable changes to **django-getlaw** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release of `django-getlaw`.
- `GETLAW` settings dict to configure API keys for `impressum`, `datenschutz`, `agb`, `widerruf`, `barrierefreiheit` (or any future text type).
- `{% load getlaw %}{% getlaw "impressum" %}` template tag returning safe HTML.
- Programmatic helpers `get_text(text_type)` and `refresh_text(text_type)` exported from `django_getlaw`.
- `python manage.py getlaw_refresh [text_type ...]` management command that warms the cache and exits non-zero on failure (suitable for cron / django-q).
- Lazy 24h cache via Django's cache framework, with a configurable TTL and key prefix.
- Optional stale-while-error fallback (`STALE_FALLBACK`, off by default) bounded by `STALE_MAX_AGE_SECONDS`.
- Detects redirect responses from the API as invalid-key errors (matches getLaw's actual behavior).
