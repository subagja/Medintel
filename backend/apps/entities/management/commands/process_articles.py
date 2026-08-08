from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.articles.models import Article
from apps.entities.services import process_article_full


@dataclass
class ProcessingSummary:
    selected: int = 0
    processed: int = 0
    eligible: int = 0
    needs_review: int = 0
    failed: int = 0
    facts_created: int = 0
    facts_skipped: int = 0


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
                "Artikel VALIDATED dan REJECTED tidak diproses ulang "
                "(kecuali dipakai bersama --force)."
            ),
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Proses ulang SEMUA artikel tanpa memandang "
                "processing_status, termasuk yang sudah VALIDATED/"
                "REJECTED. Berguna kalau processing_status sempat "
                "berubah (mis. lewat aksi validasi analis) padahal "
                "ekstraksi entitasnya sendiri belum pernah/tidak "
                "lengkap dijalankan."
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
        force = options["force"]
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
            force=force,
            limit=limit,
        )

        summary = ProcessingSummary(
            selected=len(articles),
        )

        if not articles:
            total_articles = Article.objects.count()
            excluded_count = Article.objects.filter(
                processing_status__in=[
                    Article.ProcessingStatus.VALIDATED,
                    Article.ProcessingStatus.REJECTED,
                ],
            ).count()

            self.stdout.write(
                self.style.WARNING(
                    "Tidak ada artikel yang perlu diproses."
                )
            )

            if process_all and not force and excluded_count > 0:
                self.stdout.write(
                    (
                        f"Info: {excluded_count} dari {total_articles} "
                        "artikel berstatus VALIDATED/REJECTED sehingga "
                        "dilewati oleh --all. Kalau artikel-artikel itu "
                        "sebenarnya belum pernah/tidak lengkap "
                        "diekstraksi, jalankan ulang dengan tambahan "
                        "flag --force untuk memproses ulang semuanya."
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
        force: bool,
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

        elif process_all and not force:
            queryset = queryset.exclude(
                processing_status__in=[
                    Article.ProcessingStatus.VALIDATED,
                    Article.ProcessingStatus.REJECTED,
                ],
            )

        if limit is not None:
            queryset = queryset[:limit]

        return list(queryset)

    def _process_article(
        self,
        article: Article,
    ) -> dict:
        result = process_article_full(article)

        return {
            "disease_count": result.disease_count,
            "location_count": result.location_count,
            "fact_count": result.fact_count,
            "facts_created": result.fact_result.facts_created,
            "facts_skipped": result.fact_result.facts_skipped,
            "eligibility": result.eligibility,
            "entity_result": result.entity_result,
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
