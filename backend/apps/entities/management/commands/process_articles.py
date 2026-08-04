from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.articles.models import Article
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
)
from apps.entities.services import extract_article_entities
from apps.entities.services.fact_extraction import extract_article_facts


@dataclass(frozen=True)
class ArticleEligibility:
    is_eligible: bool
    has_disease: bool
    has_location: bool
    has_numeric_fact: bool
    reason: str


@dataclass
class ProcessingSummary:
    selected: int = 0
    processed: int = 0
    eligible: int = 0
    needs_review: int = 0
    failed: int = 0
    facts_created: int = 0
    facts_skipped: int = 0


def evaluate_article_eligibility(
    article: Article,
) -> ArticleEligibility:
    has_disease = ArticleDisease.objects.filter(
        article=article,
    ).exists()

    has_location = ArticleLocation.objects.filter(
        article=article,
    ).exists()

    facts = ArticleFact.objects.filter(
        article=article,
    )

    has_numeric_fact = (
        facts.filter(case_count__isnull=False).exists()
        or facts.filter(death_count__isnull=False).exists()
    )

    missing: list[str] = []

    if not has_disease:
        missing.append("penyakit")

    if not has_location:
        missing.append("lokasi")

    if not has_numeric_fact:
        missing.append("jumlah kasus/kematian")

    if missing:
        return ArticleEligibility(
            is_eligible=False,
            has_disease=has_disease,
            has_location=has_location,
            has_numeric_fact=has_numeric_fact,
            reason=(
                "Data ekstraksi belum lengkap: "
                + ", ".join(missing)
                + "."
            ),
        )

    return ArticleEligibility(
        is_eligible=True,
        has_disease=True,
        has_location=True,
        has_numeric_fact=True,
        reason=(
            "Artikel memiliki penyakit, lokasi, "
            "dan fakta numerik."
        ),
    )


class Command(BaseCommand):
    help = (
        "Memproses artikel: ekstraksi penyakit, lokasi, fakta, "
        "penilaian kelayakan, dan pembaruan status pipeline."
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
                "Proses artikel NEW, PROCESSING, PROCESSED, atau FAILED. "
                "Artikel VALIDATED dan REJECTED tidak diproses ulang."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Batasi jumlah artikel yang diproses.",
        )

        parser.add_argument(
            "--stop-on-error",
            action="store_true",
            help="Hentikan command ketika satu artikel gagal.",
        )

    def handle(self, *args, **options):
        article_id = options["article_id"]
        process_all = options["all"]
        limit = options["limit"]
        stop_on_error = options["stop_on_error"]

        if bool(article_id) == bool(process_all):
            raise CommandError(
                "Gunakan salah satu: --article-id <UUID> atau --all."
            )

        if limit is not None and limit < 1:
            raise CommandError(
                "--limit minimal bernilai 1."
            )

        articles = self._get_articles(
            article_id=article_id,
            process_all=process_all,
            limit=limit,
        )

        summary = ProcessingSummary(
            selected=len(articles),
        )

        if not articles:
            self.stdout.write(
                self.style.WARNING(
                    "Tidak ada artikel yang perlu diproses."
                )
            )
            return

        for index, article in enumerate(
            articles,
            start=1,
        ):
            try:
                result = self._process_article(
                    article
                )
            except Exception as exc:
                summary.failed += 1

                Article.objects.filter(
                    id=article.id,
                ).update(
                    processing_status=(
                        Article.ProcessingStatus.FAILED
                    ),
                    rejection_reason=str(exc)[:1000],
                    updated_at=timezone.now(),
                )

                self.stderr.write(
                    self.style.ERROR(
                        (
                            f"[{index}/{summary.selected}] "
                            f"FAILED | {article.title} | {exc}"
                        )
                    )
                )

                if stop_on_error:
                    raise CommandError(
                        "Pemrosesan dihentikan karena "
                        "--stop-on-error aktif."
                    ) from exc

                continue

            summary.processed += 1
            summary.facts_created += result["facts_created"]
            summary.facts_skipped += result["facts_skipped"]

            eligibility = result["eligibility"]

            if eligibility.is_eligible:
                summary.eligible += 1
                style = self.style.SUCCESS
                label = "ELIGIBLE"
            else:
                summary.needs_review += 1
                style = self.style.WARNING
                label = "NEEDS_REVIEW"

            self.stdout.write(
                style(
                    (
                        f"[{index}/{summary.selected}] "
                        f"{label} | {article.title}"
                    )
                )
            )
            self.stdout.write(
                f"  Penyakit : {result['disease_count']}"
            )
            self.stdout.write(
                f"  Lokasi   : {result['location_count']}"
            )
            self.stdout.write(
                f"  Fakta    : {result['fact_count']}"
            )
            self.stdout.write(
                f"  Alasan   : {eligibility.reason}"
            )

        self._print_summary(summary)

    def _get_articles(
        self,
        *,
        article_id: UUID | None,
        process_all: bool,
        limit: int | None,
    ) -> list[Article]:
        queryset = Article.objects.order_by(
            "-created_at"
        )

        if article_id:
            queryset = queryset.filter(
                id=article_id,
            )

            if not queryset.exists():
                raise CommandError(
                    f"Artikel tidak ditemukan: {article_id}"
                )

        elif process_all:
            queryset = queryset.exclude(
                processing_status__in=[
                    Article.ProcessingStatus.VALIDATED,
                    Article.ProcessingStatus.REJECTED,
                ],
            )

        if limit is not None:
            queryset = queryset[:limit]

        return list(queryset)

    @transaction.atomic
    def _process_article(
        self,
        article: Article,
    ) -> dict:
        article.processing_status = (
            Article.ProcessingStatus.PROCESSING
        )
        article.rejection_reason = ""
        article.save(
            update_fields=[
                "processing_status",
                "rejection_reason",
                "updated_at",
            ]
        )

        entity_result = extract_article_entities(
            article
        )

        fact_result = extract_article_facts(
            article
        )

        eligibility = evaluate_article_eligibility(
            article
        )

        pipeline_metadata = {
            **(article.raw_metadata or {}),
            "processing_pipeline": {
                "processed_at": timezone.now().isoformat(),
                "eligibility": (
                    "eligible"
                    if eligibility.is_eligible
                    else "needs_review"
                ),
                "reason": eligibility.reason,
                "has_disease": eligibility.has_disease,
                "has_location": eligibility.has_location,
                "has_numeric_fact": (
                    eligibility.has_numeric_fact
                ),
            },
        }

        article.processing_status = (
            Article.ProcessingStatus.PROCESSED
        )
        article.raw_metadata = pipeline_metadata
        article.rejection_reason = ""
        article.save(
            update_fields=[
                "processing_status",
                "raw_metadata",
                "rejection_reason",
                "updated_at",
            ]
        )

        return {
            "disease_count": (
                ArticleDisease.objects.filter(
                    article=article,
                ).count()
            ),
            "location_count": (
                ArticleLocation.objects.filter(
                    article=article,
                ).count()
            ),
            "fact_count": (
                ArticleFact.objects.filter(
                    article=article,
                ).count()
            ),
            "facts_created": fact_result.facts_created,
            "facts_skipped": fact_result.facts_skipped,
            "eligibility": eligibility,
            "entity_result": entity_result,
        }

    def _print_summary(
        self,
        summary: ProcessingSummary,
    ) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Ringkasan process_articles"
            )
        )
        self.stdout.write(
            f"Artikel dipilih       : {summary.selected}"
        )
        self.stdout.write(
            f"Berhasil diproses     : {summary.processed}"
        )
        self.stdout.write(
            f"Eligible untuk review : {summary.eligible}"
        )
        self.stdout.write(
            f"Perlu review/perbaikan: {summary.needs_review}"
        )
        self.stdout.write(
            f"Gagal                 : {summary.failed}"
        )
        self.stdout.write(
            f"Fakta baru            : {summary.facts_created}"
        )
        self.stdout.write(
            f"Fakta dilewati        : {summary.facts_skipped}"
        )
