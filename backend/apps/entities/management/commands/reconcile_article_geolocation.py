from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.articles.models import Article
from apps.entities.geolocation import resolve_indonesia_locations
from apps.entities.models import (
    ArticleFact,
    ArticleLocation,
    ExtractionMethod,
    ValidationStatus,
)
from apps.entities.services import extract_article_locations


AUTOMATED_LOCATION_METHODS = (
    ExtractionMethod.SYSTEM,
    ExtractionMethod.RULE_BASED,
)


@dataclass
class ReconciliationSummary:
    selected: int = 0
    changed_articles: int = 0
    stale_automatic: int = 0
    missing_resolved: int = 0
    primary_corrections: int = 0
    protected_relations: int = 0
    facts_relocated: int = 0
    protected_facts: int = 0
    review_required: int = 0


class Command(BaseCommand):
    help = (
        "Mengaudit dan menyelaraskan ulang relasi lokasi artikel "
        "dengan resolver geolocation Indonesia terbaru."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--article-id",
            type=UUID,
            default=None,
            help="Proses satu artikel berdasarkan UUID.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Audit seluruh artikel, termasuk artikel yang sudah "
                "berstatus VALIDATED atau REJECTED."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Batasi jumlah artikel yang diaudit.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Simpan hasil rekonsiliasi. Tanpa opsi ini command "
                "hanya melakukan dry-run."
            ),
        )

    def handle(self, *args, **options):
        article_id = options["article_id"]
        process_all = options["all"]
        limit = options["limit"]
        apply_changes = options["apply"]

        if bool(article_id) == bool(process_all):
            raise CommandError(
                "Gunakan salah satu: --article-id <UUID> atau --all."
            )

        if limit is not None and limit < 1:
            raise CommandError("--limit minimal bernilai 1.")

        articles = self._get_articles(
            article_id=article_id,
            process_all=process_all,
            limit=limit,
        )
        summary = ReconciliationSummary(
            selected=len(articles),
        )

        for article in articles:
            inspection = self._inspect(article)

            if inspection["needs_review"]:
                summary.review_required += 1
                self.stdout.write(
                    f"- {article.id} | {article.title}"
                )
                self.stdout.write(
                    "  Status          : PERLU REVIEW"
                )
                self.stdout.write(
                    "  Lokasi disebut  : "
                    + (
                        ", ".join(
                            inspection["resolved_names"]
                        )
                        or "tidak ada"
                    )
                )
                self.stdout.write(
                    "  Alasan          : "
                    f"{inspection['review_reason']}"
                )
                continue

            summary.stale_automatic += len(
                inspection["stale_ids"]
            )
            summary.missing_resolved += len(
                inspection["missing_ids"]
            )
            summary.primary_corrections += int(
                inspection["primary_correction"]
            )
            summary.protected_relations += len(
                inspection["protected_ids"]
            )
            summary.facts_relocated += len(
                inspection["fact_ids_to_relocate"]
            )
            summary.protected_facts += len(
                inspection["protected_fact_ids"]
            )

            requires_change = bool(
                inspection["stale_ids"]
                or inspection["missing_ids"]
                or inspection["primary_correction"]
                or inspection["fact_ids_to_relocate"]
            )

            if not requires_change:
                continue

            summary.changed_articles += 1
            self.stdout.write(
                f"- {article.id} | {article.title}"
            )
            self.stdout.write(
                "  Lokasi utama    : "
                f"{inspection['primary_name']}"
            )
            self.stdout.write(
                "  Lokasi disebut  : "
                + ", ".join(inspection["resolved_names"])
            )
            self.stdout.write(
                "  Otomatis usang  : "
                f"{len(inspection['stale_ids'])}"
            )
            self.stdout.write(
                "  Lokasi baru     : "
                f"{len(inspection['missing_ids'])}"
            )
            self.stdout.write(
                "  Koreksi utama   : "
                f"{int(inspection['primary_correction'])}"
            )
            self.stdout.write(
                "  Fakta direlokasi: "
                f"{len(inspection['fact_ids_to_relocate'])}"
            )

            if apply_changes:
                with transaction.atomic():
                    if inspection["resolver_primary_id"]:
                        extract_article_locations(article)

                    ArticleLocation.objects.filter(
                        article=article,
                        location_id__in=inspection["stale_ids"],
                        extraction_method__in=(
                            AUTOMATED_LOCATION_METHODS
                        ),
                        validation_status=(
                            ValidationStatus.UNREVIEWED
                        ),
                    ).delete()

                    automatic_relations = (
                        ArticleLocation.objects.filter(
                            article=article,
                            location__country_code="ID",
                            extraction_method__in=(
                                AUTOMATED_LOCATION_METHODS
                            ),
                            validation_status=(
                                ValidationStatus.UNREVIEWED
                            ),
                        )
                    )
                    automatic_relations.update(
                        is_primary=False,
                    )

                    if not inspection["protected_primary"]:
                        automatic_relations.filter(
                            location_id=inspection["primary_id"],
                        ).update(
                            is_primary=True,
                        )

                    ArticleFact.objects.filter(
                        id__in=(
                            inspection[
                                "fact_ids_to_relocate"
                            ]
                        ),
                    ).update(
                        location_id=inspection["primary_id"],
                    )

        self._print_summary(
            summary,
            apply_changes=apply_changes,
        )

    def _get_articles(
        self,
        *,
        article_id: UUID | None,
        process_all: bool,
        limit: int | None,
    ) -> list[Article]:
        queryset = Article.objects.order_by("-created_at")

        if article_id:
            queryset = queryset.filter(id=article_id)

            if not queryset.exists():
                raise CommandError(
                    f"Artikel tidak ditemukan: {article_id}"
                )
        elif not process_all:
            return []

        if limit is not None:
            queryset = queryset[:limit]

        return list(queryset)

    @staticmethod
    def _inspect(article: Article) -> dict:
        text = (
            f"{article.title}\n{article.content_text}"
        ).replace("\xa0", " ")
        geolocation = resolve_indonesia_locations(
            text,
            title_length=len(article.title),
        )
        resolved_names = [
            item.location_name
            for item in geolocation.mentions
        ]
        primary_id = (
            geolocation.primary.location_id
            if geolocation.primary
            else None
        )
        resolver_primary_id = primary_id

        relations = list(
            ArticleLocation.objects.filter(
                article=article,
                location__country_code="ID",
            ).select_related("location")
        )
        automatic = [
            relation
            for relation in relations
            if relation.extraction_method
            in AUTOMATED_LOCATION_METHODS
            and relation.validation_status
            == ValidationStatus.UNREVIEWED
        ]
        protected = [
            relation
            for relation in relations
            if relation not in automatic
        ]
        protected_primaries = [
            relation
            for relation in protected
            if relation.is_primary
        ]

        if len(protected_primaries) > 1:
            return {
                "resolved_names": resolved_names,
                "needs_review": True,
                "review_reason": (
                    "lebih dari satu lokasi utama hasil penilaian analis"
                ),
            }

        protected_primary = (
            protected_primaries[0]
            if protected_primaries
            else None
        )

        if protected_primary:
            primary_id = str(protected_primary.location_id)
            primary_name = (
                f"{protected_primary.location.name} (analis)"
            )
        elif geolocation.primary:
            primary_name = geolocation.primary.location_name
        else:
            return {
                "resolved_names": resolved_names,
                "needs_review": True,
                "review_reason": (
                    "tidak ada satu lokasi kejadian utama yang tegas"
                    if resolved_names
                    else "lokasi domestik tidak berhasil diresolusi"
                ),
            }

        target_ids = {primary_id}
        current_ids = {
            str(relation.location_id)
            for relation in relations
        }
        stale_ids = {
            str(relation.location_id)
            for relation in automatic
            if str(relation.location_id) not in target_ids
        }
        protected_ids = {
            str(relation.location_id)
            for relation in protected
            if str(relation.location_id) not in target_ids
        }
        automatic_primary_ids = {
            str(relation.location_id)
            for relation in automatic
            if relation.is_primary
        }
        expected_automatic_primary_ids = (
            set()
            if protected_primary
            else {primary_id}
        )
        mismatched_domestic_facts = [
            fact
            for fact in ArticleFact.objects.filter(
                article=article,
            ).select_related("location")
            if (
                fact.location_id is None
                or fact.location.country_code == "ID"
            )
            and (
                str(fact.location_id)
                if fact.location_id
                else None
            )
            != primary_id
        ]
        fact_ids_to_relocate = {
            str(fact.id)
            for fact in mismatched_domestic_facts
            if fact.extraction_method
            in AUTOMATED_LOCATION_METHODS
            and fact.validation_status
            == ValidationStatus.UNREVIEWED
        }
        protected_fact_ids = {
            str(fact.id)
            for fact in mismatched_domestic_facts
            if str(fact.id) not in fact_ids_to_relocate
        }

        return {
            "resolved_names": resolved_names,
            "needs_review": False,
            "review_reason": "",
            "primary_id": primary_id,
            "primary_name": primary_name,
            "resolver_primary_id": resolver_primary_id,
            "protected_primary": bool(protected_primary),
            "stale_ids": stale_ids,
            "missing_ids": target_ids - current_ids,
            "primary_correction": (
                automatic_primary_ids
                != expected_automatic_primary_ids
            ),
            "protected_ids": protected_ids,
            "fact_ids_to_relocate": fact_ids_to_relocate,
            "protected_fact_ids": protected_fact_ids,
        }

    def _print_summary(
        self,
        summary: ReconciliationSummary,
        *,
        apply_changes: bool,
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Ringkasan rekonsiliasi geolocation artikel"
            )
        )
        self.stdout.write(
            f"Artikel diaudit       : {summary.selected}"
        )
        self.stdout.write(
            f"Artikel perlu koreksi : {summary.changed_articles}"
        )
        self.stdout.write(
            f"Artikel perlu review  : {summary.review_required}"
        )
        self.stdout.write(
            f"Relasi otomatis usang : {summary.stale_automatic}"
        )
        self.stdout.write(
            f"Lokasi resolver baru  : {summary.missing_resolved}"
        )
        self.stdout.write(
            f"Lokasi utama dikoreksi: {summary.primary_corrections}"
        )
        self.stdout.write(
            f"Relasi analis dijaga  : {summary.protected_relations}"
        )
        self.stdout.write(
            f"Fakta direlokasi      : {summary.facts_relocated}"
        )
        self.stdout.write(
            f"Fakta analis dijaga   : {summary.protected_facts}"
        )
        self.stdout.write(
            f"Perubahan disimpan    : {apply_changes}"
        )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: database tidak berubah. Jalankan "
                    "kembali dengan --apply setelah output diperiksa."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Rekonsiliasi geolocation artikel selesai."
                )
            )
