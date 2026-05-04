"""``manage.py getlaw_refresh`` — warm the cache for one or all text types."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from django_getlaw.core import GetlawError, configured_text_types, refresh_text


class Command(BaseCommand):
    help = (
        "Refresh getLaw legal texts from the API and write them to the cache. "
        "With no arguments, refreshes every text type listed in GETLAW['KEYS']. "
        "Exits with a non-zero status if any refresh fails."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "text_types",
            nargs="*",
            help="Text types to refresh (e.g. impressum datenschutz). "
            "Defaults to every type configured in settings.GETLAW['KEYS'].",
        )

    def handle(self, *args, **options):
        requested = options["text_types"] or configured_text_types()
        if not requested:
            raise CommandError(
                "No text types to refresh. Configure settings.GETLAW['KEYS'] "
                "or pass text types as positional arguments."
            )

        failed: list[tuple[str, str]] = []
        for text_type in requested:
            try:
                content = refresh_text(text_type)
            except GetlawError as exc:
                failed.append((text_type, str(exc)))
                self.stderr.write(self.style.ERROR(f"  FAIL  {text_type}: {exc}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  OK    {text_type} ({len(content)} chars)"))

        if failed:
            raise CommandError(
                f"{len(failed)} of {len(requested)} text(s) failed to refresh: "
                + ", ".join(t for t, _ in failed)
            )
