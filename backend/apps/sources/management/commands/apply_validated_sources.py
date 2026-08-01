from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction

from apps.sources.models import Source
from apps.sources.services import (
    check_source_crawl_readiness,
)


class Command(BaseCommand):
    help = (
        "Menerapkan hasil validasi source secara batch. "
        "Secara default hanya menampilkan dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Path file validated_sources.json.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Benar-benar menyimpan perubahan. "
                "Tanpa opsi ini command hanya dry-run."
            ),
        )
        parser.add_argument(
            "--disable-manual-review",
            action="store_true",
            help=(
                "Set crawl_enabled=False untuk source "
                "dalam kelompok manual_review."
            ),
        )
        parser.add_argument(
            "--disable-keep-disabled",
            action="store_true",
            help=(
                "Set crawl_enabled=False untuk source "
                "dalam kelompok keep_disabled."
            ),
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        should_apply = options["apply"]
        disable_manual = options["disable_manual_review"]
        disable_keep = options["disable_keep_disabled"]

        if not file_path.exists():
            raise CommandError(
                f"File tidak ditemukan: {file_path}"
            )

        try:
            payload = json.loads(
                file_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise CommandError(
                f"JSON tidak valid: {exc}"
            ) from exc

        actions = payload.get("actions")

        if not isinstance(actions, dict):
            raise CommandError(
                "File tidak memiliki object 'actions'."
            )

        keep_enabled = self._read_codes(
            actions,
            "keep_enabled",
        )
        enable_crawling = self._read_codes(
            actions,
            "enable_crawling",
        )
        manual_review = self._read_codes(
            actions,
            "manual_review",
        )
        keep_disabled = self._read_codes(
            actions,
            "keep_disabled",
        )

        requested_codes = set().union(
            keep_enabled,
            enable_crawling,
            manual_review,
            keep_disabled,
        )

        existing_sources = {
            source.code: source
            for source in Source.objects.filter(
                code__in=requested_codes
            )
        }

        missing_codes = sorted(
            requested_codes
            - set(existing_sources)
        )

        if missing_codes:
            raise CommandError(
                "Source tidak ditemukan: "
                + ", ".join(missing_codes)
            )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Rencana pembaruan source"
            )
        )
        self.stdout.write(
            f"Mode                  : "
            f"{'APPLY' if should_apply else 'DRY-RUN'}"
        )
        self.stdout.write(
            f"Pertahankan aktif     : {len(keep_enabled)}"
        )
        self.stdout.write(
            f"Aktifkan crawling     : {len(enable_crawling)}"
        )
        self.stdout.write(
            f"Review manual         : {len(manual_review)}"
        )
        self.stdout.write(
            f"Tetap dinonaktifkan   : {len(keep_disabled)}"
        )

        changes: list[tuple[str, bool, bool]] = []

        for code in sorted(keep_enabled | enable_crawling):
            source = existing_sources[code]
            old_value = source.crawl_enabled
            new_value = True

            readiness = check_source_crawl_readiness(
                source
            )

            readiness_errors = [
                error
                for error in readiness.errors
                if error != "Crawling belum diaktifkan."
            ]

            if readiness_errors:
                self.stderr.write(
                    self.style.ERROR(
                        f"- {code} tidak diaktifkan: "
                        + "; ".join(readiness_errors)
                    )
                )
                continue

            changes.append(
                (code, old_value, new_value)
            )

        if disable_manual:
            for code in sorted(manual_review):
                source = existing_sources[code]
                changes.append(
                    (
                        code,
                        source.crawl_enabled,
                        False,
                    )
                )

        if disable_keep:
            for code in sorted(keep_disabled):
                source = existing_sources[code]
                changes.append(
                    (
                        code,
                        source.crawl_enabled,
                        False,
                    )
                )

        self.stdout.write("")

        for code, old_value, new_value in changes:
            marker = (
                "UNCHANGED"
                if old_value == new_value
                else "CHANGE"
            )
            self.stdout.write(
                f"[{marker}] {code}: "
                f"{old_value} -> {new_value}"
            )

        if not should_apply:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run selesai. Tambahkan --apply "
                    "untuk menyimpan perubahan."
                )
            )
            return

        with transaction.atomic():
            changed_count = 0

            for code, old_value, new_value in changes:
                if old_value == new_value:
                    continue

                source = existing_sources[code]
                source.crawl_enabled = new_value

                note = (
                    "Status crawling diperbarui dari hasil "
                    "audit source batch."
                )

                if note not in source.crawler_notes:
                    source.crawler_notes = (
                        f"{source.crawler_notes}\n{note}"
                    ).strip()

                source.save(
                    update_fields=[
                        "crawl_enabled",
                        "crawler_notes",
                        "updated_at",
                    ]
                )

                changed_count += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Pembaruan selesai. "
                f"{changed_count} source berubah."
            )
        )

    def _read_codes(
        self,
        actions: dict,
        key: str,
    ) -> set[str]:
        value = actions.get(key, [])

        if not isinstance(value, list):
            raise CommandError(
                f"actions.{key} harus berupa list."
            )

        codes = set()

        for item in value:
            if not isinstance(item, str):
                raise CommandError(
                    f"actions.{key} berisi nilai non-string."
                )

            code = item.strip()

            if code:
                codes.add(code)

        return codes
