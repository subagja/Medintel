from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import transaction

from apps.sources.models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)


class Command(BaseCommand):
    help = (
        "Menerapkan perbaikan seed dan allow pattern "
        "untuk source mainstream secara batch."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            help="Path mainstream_source_updates.json.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Simpan perubahan. Tanpa opsi ini "
                "command hanya dry-run."
            ),
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        should_apply = options["apply"]

        if not file_path.exists():
            raise CommandError(
                f"File tidak ditemukan: {file_path}"
            )

        try:
            payload = json.loads(
                file_path.read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as exc:
            raise CommandError(
                f"JSON tidak valid: {exc}"
            ) from exc

        items = payload.get("sources", [])

        if not isinstance(items, list):
            raise CommandError(
                "'sources' harus berupa list."
            )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Rencana pembaruan source mainstream"
            )
        )
        self.stdout.write(
            f"Mode: "
            f"{'APPLY' if should_apply else 'DRY-RUN'}"
        )

        for item in items:
            self._validate_item(item)

            code = item["code"]
            source = Source.objects.filter(
                code=code
            ).first()

            if source is None:
                raise CommandError(
                    f"Source tidak ditemukan: {code}"
                )

            self.stdout.write("")
            self.stdout.write(
                self.style.HTTP_INFO(
                    f"Source: {source.name} ({code})"
                )
            )

            for seed in item.get("seeds", []):
                self.stdout.write(
                    f"  seed  : {seed['url']}"
                )

            for pattern in item.get(
                "allow_patterns",
                [],
            ):
                self.stdout.write(
                    f"  allow : {pattern['pattern']}"
                )

            self.stdout.write(
                "  status: tetap disabled sampai audit PASS"
            )

        if not should_apply:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run selesai. Tambahkan --apply "
                    "untuk menyimpan."
                )
            )
            return

        with transaction.atomic():
            for item in items:
                source = Source.objects.get(
                    code=item["code"]
                )

                if item.get(
                    "replace_active_seeds",
                    False,
                ):
                    source.seed_urls.filter(
                        is_active=True
                    ).update(
                        is_active=False
                    )

                if item.get(
                    "replace_active_allow_patterns",
                    False,
                ):
                    source.url_patterns.filter(
                        is_active=True,
                        pattern_type=(
                            SourceUrlPattern.PatternType.ALLOW
                        ),
                    ).update(
                        is_active=False
                    )

                for seed_data in item.get(
                    "seeds",
                    [],
                ):
                    SourceSeedUrl.objects.update_or_create(
                        source=source,
                        url=seed_data["url"],
                        defaults={
                            "seed_type": seed_data["seed_type"],
                            "priority": seed_data.get(
                                "priority",
                                100,
                            ),
                            "is_active": seed_data.get(
                                "is_active",
                                True,
                            ),
                            "notes": seed_data.get(
                                "notes",
                                "",
                            ),
                        },
                    )

                for pattern_data in item.get(
                    "allow_patterns",
                    [],
                ):
                    SourceUrlPattern.objects.update_or_create(
                        source=source,
                        pattern_type=(
                            SourceUrlPattern.PatternType.ALLOW
                        ),
                        match_type=pattern_data["match_type"],
                        pattern=pattern_data["pattern"],
                        defaults={
                            "priority": pattern_data.get(
                                "priority",
                                100,
                            ),
                            "description": pattern_data.get(
                                "description",
                                "",
                            ),
                            "is_active": True,
                        },
                    )

                # Konfigurasi diperbaiki dahulu.
                # Aktivasi dilakukan setelah audit PASS.
                source.crawl_enabled = False
                source.crawler_notes = (
                    (
                        f"{source.crawler_notes}\n"
                        "Seed dan allow pattern diperbarui; "
                        "menunggu audit ulang."
                    )
                    .strip()
                )
                source.save(
                    update_fields=[
                        "crawl_enabled",
                        "crawler_notes",
                        "updated_at",
                    ]
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Pembaruan source mainstream selesai."
            )
        )

    def _validate_item(self, item):
        if not isinstance(item, dict):
            raise CommandError(
                "Setiap source harus berupa object."
            )

        if not item.get("code"):
            raise CommandError(
                "Setiap source wajib memiliki code."
            )

        for seed in item.get("seeds", []):
            if not seed.get("url"):
                raise CommandError(
                    "Seed wajib memiliki url."
                )

        for pattern in item.get(
            "allow_patterns",
            [],
        ):
            if not pattern.get("pattern"):
                raise CommandError(
                    "Allow pattern wajib memiliki pattern."
                )
