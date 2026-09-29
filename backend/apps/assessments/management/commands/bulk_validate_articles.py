"""Validasi otomatis artikel hasil crawling secara massal.

Rule engine menangani artikel dengan bukti yang sudah lengkap. Artikel yang
belum dapat diputuskan oleh rule engine dikirim ke OpenAI. Hasil yang yakin
diterapkan sebagai ``validated`` atau ``rejected``; hasil ambigu tetap
``pending`` untuk diperiksa analis.

Contoh:
    python manage.py bulk_validate_articles --dry-run
    python manage.py bulk_validate_articles --limit 20 --skip-ai
    python manage.py bulk_validate_articles --limit 50
    python manage.py bulk_validate_articles --limit 20 --recommend-only
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Literal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import (
    ArticleValidationAssessment,
    ArticleValidationHistory,
    BulkArticleValidationJob,
)
from apps.assessments.services.information_balance import (
    has_complete_structured_evidence,
    recommend_information_balance,
)
from apps.assessments.services.article_data_review import reextract_article_data
from apps.assessments.services.ai_evidence import apply_ai_evidence


CONFIDENT_CREDIBILITY = {1, 2, 3}
CONFIDENT_SOURCE = {"A", "B", "C"}

AI_MODEL = os.environ.get("OPENAI_ASSESSMENT_MODEL", "gpt-6-luna")
AI_MAX_RETRIES = 3
AI_SLEEP_BETWEEN_CALLS = 0.5
AUTO_ASSESSMENT_VERSION = "1.4"

# Artikel hasil crawling merupakan informasi open source. Dalam standar
# penilaian aplikasi, kualitas tertingginya dibatasi pada C3: A/B menjadi C
# dan kredibilitas 1/2 menjadi 3. Nilai yang lebih rendah tidak dinaikkan.
OPEN_SOURCE_RELIABILITY_CEILING = "C"
OPEN_SOURCE_CREDIBILITY_CEILING = 3


@dataclass(frozen=True)
class Candidate:
    article: Article
    assessment: ArticleValidationAssessment | None


class Command(BaseCommand):
    help = (
        "Validasi artikel secara massal: rule-based dahulu, lalu AI untuk "
        "artikel yang belum dapat diputuskan."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulasikan seluruh proses tanpa menulis ke database.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Batasi jumlah artikel pada satu eksekusi.",
        )
        parser.add_argument(
            "--skip-ai",
            action="store_true",
            help=(
                "Simpan hasil yang belum meyakinkan sebagai Perlu Review "
                "tanpa memanggil OpenAI."
            ),
        )
        parser.add_argument(
            "--recommend-only",
            action="store_true",
            help=(
                "Simpan rekomendasi tanpa mengubah status final artikel."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Proses ulang hasil otomatis yang masih pending. "
                "Keputusan final analis tetap tidak disentuh."
            ),
        )
        parser.add_argument(
            "--job-id",
            default=None,
            help=(
                "ID job dashboard untuk pencatatan progres. "
                "Tidak diperlukan saat command dijalankan manual."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        skip_ai = options["skip_ai"]
        recommend_only = options["recommend_only"]
        force = options["force"]
        job = self._start_job(options.get("job_id"))

        if limit is not None and limit < 1:
            raise CommandError("--limit minimal bernilai 1.")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "=== DRY RUN - tidak ada perubahan disimpan ==="
                )
            )

        candidates = self._collect_candidates(
            force=force,
            limit=limit,
            skip_ai=skip_ai,
            recommend_only=recommend_only,
        )
        self.stdout.write(
            f"Memproses {len(candidates)} kandidat validasi otomatis..."
        )

        if job is not None:
            job.total_items = len(candidates)
            job.save(update_fields=["total_items", "updated_at"])

        stats = {
            "rule_recommend_validate": 0,
            "ai_recommend_validate": 0,
            "ai_recommend_reject": 0,
            "needs_review": 0,
            "validated": 0,
            "rejected": 0,
            "pending": 0,
            "errors": 0,
        }

        for candidate in candidates:
            try:
                self._process_one(
                    candidate=candidate,
                    dry_run=dry_run,
                    skip_ai=skip_ai,
                    recommend_only=recommend_only,
                    stats=stats,
                )
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"[ERROR] {candidate.article.id} "
                        f"({candidate.article.title[:50]}): {exc}"
                    )
                )
            finally:
                if job is not None:
                    job.processed_items += 1
                    job.error_count = stats["errors"]
                    job.save(
                        update_fields=[
                            "processed_items",
                            "error_count",
                            "updated_at",
                        ]
                    )

        self.stdout.write(self.style.SUCCESS(f"Selesai. Ringkasan: {stats}"))

        if job is not None:
            job.status = BulkArticleValidationJob.Status.COMPLETED
            job.summary = stats
            job.completed_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "summary",
                    "completed_at",
                    "updated_at",
                ]
            )

    @staticmethod
    def _start_job(job_id: str | None):
        if not job_id:
            return None

        try:
            job = BulkArticleValidationJob.objects.get(pk=job_id)
        except (BulkArticleValidationJob.DoesNotExist, ValueError) as exc:
            raise CommandError("Job bulk validation tidak ditemukan.") from exc

        if job.status != BulkArticleValidationJob.Status.QUEUED:
            raise CommandError("Job bulk validation tidak lagi menunggu.")

        job.status = BulkArticleValidationJob.Status.RUNNING
        job.started_at = timezone.now()
        job.error_message = ""
        job.save(
            update_fields=[
                "status",
                "started_at",
                "error_message",
                "updated_at",
            ]
        )
        return job

    def _collect_candidates(
        self,
        *,
        force: bool,
        limit: int | None,
        skip_ai: bool,
        recommend_only: bool,
    ) -> list[Candidate]:
        missing_articles = (
            Article.objects.filter(
                processing_status=Article.ProcessingStatus.PROCESSED,
                validation_assessment__isnull=True,
            )
            .select_related("source")
            .order_by("crawled_at")
        )

        pending_assessments = (
            ArticleValidationAssessment.objects.filter(
                validation_status=(
                    ArticleValidationAssessment.ValidationStatus.PENDING
                ),
                article__processing_status=Article.ProcessingStatus.PROCESSED,
            )
            .select_related("article", "article__source")
            .order_by("evaluated_at")
        )

        if not force:
            if recommend_only or skip_ai:
                pending_assessments = pending_assessments.filter(
                    auto_assessed_at__isnull=True,
                )
            else:
                pending_assessments = pending_assessments.filter(
                    Q(auto_assessed_at__isnull=True)
                    | Q(
                        auto_assessment_method=(
                            ArticleValidationAssessment
                            .AutoAssessmentMethod
                            .RULE_BASED
                        )
                    )
                )

        missing_slice = (
            missing_articles
            if limit is None
            else missing_articles[:limit]
        )
        candidates = [
            Candidate(article=article, assessment=None)
            for article in missing_slice
        ]

        remaining = None if limit is None else limit - len(candidates)
        if remaining == 0:
            return candidates

        pending_slice = (
            pending_assessments
            if remaining is None
            else pending_assessments[:remaining]
        )
        candidates.extend(
            Candidate(article=item.article, assessment=item)
            for item in pending_slice
        )

        return candidates

    def _process_one(
        self,
        *,
        candidate: Candidate,
        dry_run: bool,
        skip_ai: bool,
        recommend_only: bool,
        stats: dict,
    ) -> None:
        article = candidate.article
        # Re-extract missing data before assigning a final status. A dry run
        # evaluates the same data while rolling back all extraction writes.
        if not has_complete_structured_evidence(article):
            with transaction.atomic():
                reextract_article_data(article)
                if dry_run:
                    recommendation = recommend_information_balance(article)
                    evidence_complete = has_complete_structured_evidence(article)
                    transaction.set_rollback(True)
        if not dry_run or has_complete_structured_evidence(article):
            recommendation = recommend_information_balance(article)
            evidence_complete = has_complete_structured_evidence(article)
        confident = (
            recommendation.information_credibility
            in CONFIDENT_CREDIBILITY
            and recommendation.source_reliability in CONFIDENT_SOURCE
        )

        if confident and evidence_complete:
            auto_recommendation = (
                ArticleValidationAssessment
                .AutoRecommendation
                .RECOMMEND_VALIDATE
            )
            method = (
                ArticleValidationAssessment
                .AutoAssessmentMethod
                .RULE_BASED
            )
            source_reliability = recommendation.source_reliability
            information_credibility = recommendation.information_credibility
            relevance_notes = (
                "Direkomendasikan relevan oleh pra-assessment otomatis "
                f"({recommendation.admiralty_code})."
            )
            assessment_notes = recommendation.suggested_notes
            stats["rule_recommend_validate"] += 1
        elif skip_ai:
            auto_recommendation = (
                ArticleValidationAssessment
                .AutoRecommendation
                .NEEDS_REVIEW
            )
            method = (
                ArticleValidationAssessment
                .AutoAssessmentMethod
                .RULE_BASED
            )
            source_reliability = recommendation.source_reliability
            information_credibility = recommendation.information_credibility
            relevance_notes = (
                "Belum dapat direkomendasikan otomatis; pemeriksaan analis "
                "diperlukan."
            )
            assessment_notes = recommendation.suggested_notes
            stats["needs_review"] += 1
        else:
            ai_result = self._call_ai_assessment(article, recommendation)
            if (
                not evidence_complete
                and ai_result.get("evidence")
                and ai_result["auto_recommendation"] != "recommend_reject"
            ):
                with transaction.atomic():
                    evidence_saved = apply_ai_evidence(
                        article, ai_result["evidence"],
                    )
                    evidence_complete = has_complete_structured_evidence(article)
                    if dry_run:
                        transaction.set_rollback(True)
                if evidence_saved:
                    assessment_notes_extra = " Bukti AI dicocokkan dengan teks artikel."
                else:
                    assessment_notes_extra = " Usulan bukti AI tidak lolos verifikasi."
            else:
                assessment_notes_extra = ""
            auto_recommendation = ai_result["auto_recommendation"]
            method = (
                ArticleValidationAssessment
                .AutoAssessmentMethod
                .AI_ASSISTED
            )
            source_reliability = ai_result["source_reliability"]
            information_credibility = ai_result["information_credibility"]
            relevance_notes = ai_result["relevance_notes"]
            assessment_notes = (
                "[AI-assisted] " + ai_result["assessment_notes"]
                + assessment_notes_extra
            )

            if auto_recommendation == (
                ArticleValidationAssessment
                .AutoRecommendation
                .RECOMMEND_VALIDATE
            ):
                stats["ai_recommend_validate"] += 1
            elif auto_recommendation == (
                ArticleValidationAssessment
                .AutoRecommendation
                .RECOMMEND_REJECT
            ):
                stats["ai_recommend_reject"] += 1
            else:
                stats["needs_review"] += 1

            time.sleep(AI_SLEEP_BETWEEN_CALLS)

        if (
            not evidence_complete
            and auto_recommendation
            == ArticleValidationAssessment.AutoRecommendation.RECOMMEND_VALIDATE
        ):
            if method == ArticleValidationAssessment.AutoAssessmentMethod.AI_ASSISTED:
                stats["ai_recommend_validate"] -= 1
            auto_recommendation = ArticleValidationAssessment.AutoRecommendation.NEEDS_REVIEW
            information_credibility = 6
            assessment_notes += (
                " Bukti terstruktur belum lengkap: diperlukan penyakit, "
                "tepat satu lokasi utama, serta fakta angka yang terkait "
                "dengan keduanya dan memiliki kutipan isi artikel."
            )
            stats["needs_review"] += 1

        source_reliability, information_credibility = (
            self._enforce_open_source_ceiling(
                source_reliability=source_reliability,
                information_credibility=information_credibility,
            )
        )

        target_status = self._target_status(
            auto_recommendation=auto_recommendation,
            recommend_only=recommend_only,
        )
        stats[target_status] += 1

        self.stdout.write(
            f"  [{method}] {article.id} {article.title[:60]!r} -> "
            f"{auto_recommendation} {source_reliability}"
            f"{information_credibility} (status={target_status})"
        )

        if dry_run:
            return

        with transaction.atomic():
            assessment = candidate.assessment
            if assessment is None:
                assessment = ArticleValidationAssessment.objects.create(
                    article=article,
                    validation_status=(
                        ArticleValidationAssessment.ValidationStatus.PENDING
                    ),
                )

            previous_status = assessment.validation_status
            previous_source_reliability = assessment.source_reliability
            previous_information_credibility = (
                assessment.information_credibility
            )

            assessment.source_reliability = source_reliability
            assessment.information_credibility = information_credibility
            assessment.relevance_notes = relevance_notes
            assessment.assessment_notes = assessment_notes
            assessment.supporting_factors = list(
                recommendation.supporting_factors
            )
            assessment.limiting_factors = list(
                recommendation.limiting_factors
            )
            assessment.auto_recommendation = auto_recommendation
            assessment.auto_assessment_method = method
            assessment.auto_assessment_version = AUTO_ASSESSMENT_VERSION
            assessment.auto_assessed_at = timezone.now()
            assessment.validation_status = target_status
            assessment.save()

            if target_status == (
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            ):
                article.processing_status = Article.ProcessingStatus.VALIDATED
                article.rejection_reason = ""
            elif target_status == (
                ArticleValidationAssessment.ValidationStatus.REJECTED
            ):
                article.processing_status = Article.ProcessingStatus.REJECTED
                article.rejection_reason = relevance_notes
            else:
                article.processing_status = Article.ProcessingStatus.PROCESSED

            article.save(
                update_fields=[
                    "processing_status",
                    "rejection_reason",
                    "updated_at",
                ]
            )

            ArticleValidationHistory.objects.create(
                assessment=assessment,
                previous_status=previous_status,
                new_status=target_status,
                previous_source_reliability=previous_source_reliability,
                new_source_reliability=source_reliability,
                previous_information_credibility=(
                    previous_information_credibility
                ),
                new_information_credibility=information_credibility,
                change_notes=(
                    "Validasi otomatis "
                    f"({method}, rekomendasi={auto_recommendation}, "
                    f"versi={AUTO_ASSESSMENT_VERSION})."
                ),
                changed_by=None,
            )

    @staticmethod
    def _enforce_open_source_ceiling(
        *,
        source_reliability: str,
        information_credibility: int,
    ) -> tuple[str, int]:
        """Batasi penilaian artikel open source pada kualitas tertinggi C3."""
        reliability = str(source_reliability).strip().upper()
        credibility = int(information_credibility)

        if reliability in {"A", "B"}:
            reliability = OPEN_SOURCE_RELIABILITY_CEILING
        if credibility in {1, 2}:
            credibility = OPEN_SOURCE_CREDIBILITY_CEILING

        return reliability, credibility

    @staticmethod
    def _target_status(*, auto_recommendation: str, recommend_only: bool):
        if recommend_only:
            return ArticleValidationAssessment.ValidationStatus.PENDING
        if auto_recommendation == (
            ArticleValidationAssessment.AutoRecommendation.RECOMMEND_VALIDATE
        ):
            return ArticleValidationAssessment.ValidationStatus.VALIDATED
        if auto_recommendation == (
            ArticleValidationAssessment.AutoRecommendation.RECOMMEND_REJECT
        ):
            return ArticleValidationAssessment.ValidationStatus.REJECTED
        return ArticleValidationAssessment.ValidationStatus.PENDING

    def _call_ai_assessment(self, article, recommendation) -> dict:
        """Minta keputusan otomatis dengan jalur pending untuk kasus ambigu."""
        from openai import OpenAI
        from pydantic import BaseModel

        class ExtractedEvidence(BaseModel):
            disease_name: str | None
            location_name: str | None
            case_count: int | None
            death_count: int | None
            evidence_quote: str | None

        class AIAssessmentResult(BaseModel):
            auto_recommendation: Literal[
                "recommend_validate",
                "needs_review",
                "recommend_reject",
            ]
            source_reliability: Literal["C", "D", "E", "F"]
            information_credibility: int
            relevance_notes: str
            assessment_notes: str
            evidence: ExtractedEvidence | None

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY belum tersedia.")

        client = OpenAI(api_key=api_key, timeout=45.0, max_retries=0)
        content_excerpt = (article.content_text or "")[:6000]
        prompt = f"""Kamu membantu validasi otomatis artikel OSINT penyakit menular.
Hasil yang yakin akan diterapkan otomatis. Gunakan needs_review apabila bukti
tidak cukup atau kasusnya ambigu.

Judul: {article.title}
Sumber: {article.source.name} (terverifikasi: {article.source.is_verified})
Diterbitkan: {article.published_at}

Isi artikel (potongan):
\"\"\"
{content_excerpt}
\"\"\"

Hasil rule engine:
- reliabilitas sumber: {recommendation.source_reliability}
- kredibilitas informasi: {recommendation.information_credibility}
- alasan sumber: {recommendation.source_reason}
- alasan informasi: {recommendation.information_reason}

Nilai apakah artikel relevan sebagai informasi awal surveilans penyakit
menular. Karena artikel merupakan informasi open source, nilai tertinggi yang
boleh diberikan adalah C3. Jangan gunakan reliabilitas A/B atau kredibilitas
1/2. Balas HANYA JSON tanpa markdown:
{{
  "auto_recommendation": "recommend_validate", "needs_review", atau "recommend_reject",
  "source_reliability": "C", "D", "E", atau "F",
  "information_credibility": angka 3 sampai 6,
  "relevance_notes": "alasan singkat rekomendasi",
  "assessment_notes": "dasar singkat Admiralty Code",
  "evidence": {{
    "disease_name": "nama penyakit dalam teks atau null",
    "location_name": "satu lokasi kejadian dalam teks atau null",
    "case_count": "angka kasus pasti atau null",
    "death_count": "angka kematian pasti atau null",
    "evidence_quote": "kutipan pendek PERSIS dari satu bagian teks di atas yang memuat penyakit, lokasi, dan angka, atau null"
  }} atau null
}}"""

        last_error = None
        valid_recommendations = {
            ArticleValidationAssessment.AutoRecommendation.RECOMMEND_VALIDATE,
            ArticleValidationAssessment.AutoRecommendation.NEEDS_REVIEW,
            ArticleValidationAssessment.AutoRecommendation.RECOMMEND_REJECT,
        }

        for attempt in range(AI_MAX_RETRIES):
            try:
                response = client.chat.completions.parse(
                    model=AI_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Klasifikasikan artikel secara konservatif "
                                "untuk validasi OSINT penyakit menular."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    response_format=AIAssessmentResult,
                )
                parsed = response.choices[0].message.parsed
                if parsed is None:
                    raise ValueError("OpenAI tidak mengembalikan hasil terstruktur.")
                data = parsed.model_dump()

                if data["auto_recommendation"] not in valid_recommendations:
                    raise ValueError("Rekomendasi AI tidak valid.")
                if data["source_reliability"] not in {"C", "D", "E", "F"}:
                    raise ValueError("Reliabilitas AI tidak valid.")
                credibility = int(data["information_credibility"])
                if credibility not in range(3, 7):
                    raise ValueError("Kredibilitas AI tidak valid.")

                data["information_credibility"] = credibility
                data["relevance_notes"] = str(
                    data.get("relevance_notes", "")
                ).strip()
                data["assessment_notes"] = str(
                    data.get("assessment_notes", "")
                ).strip()
                return data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(1.5 * (attempt + 1))

        raise RuntimeError(
            "Gagal mendapat hasil AI setelah "
            f"{AI_MAX_RETRIES} percobaan: {last_error}"
        )
