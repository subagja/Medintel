from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Roles
from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.collection.models import CollectionJob, CollectionJobItem
from apps.entities.models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    ValidationStatus,
)
from apps.locations.models import Location
from apps.signals.models import Signal, SignalHistory
from apps.sources.models import Source

from .criteria import UAT_TASKS, criteria_for
from .models import EvaluationRecord, QualityMetricSnapshot
from .services import build_quality_report, completion_blockers


User = get_user_model()


class QualityEvaluationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.analyst = self._user("quality-analyst", Roles.ANALYST)
        self.reviewer = self._user("quality-reviewer", Roles.REVIEWER)
        self.viewer = self._user("quality-viewer", Roles.VIEWER)
        self.source = Source.objects.create(
            name="Media Mutu",
            code="media-mutu",
            domain="mutu.example.com",
            base_url="https://mutu.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
        )
        self.disease = Disease.objects.create(name="DBD Mutu", code="dbd-mutu")
        self.location = Location.objects.create(
            name="Kota Mutu",
            administrative_level=Location.AdministrativeLevel.CITY,
            country_code="ID",
        )

    def _user(self, username, role):
        user = User.objects.create_user(username=username, password="test-password-123")
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    def _article(self, sequence):
        article = Article.objects.create(
            source=self.source,
            original_url=f"https://mutu.example.com/article-{sequence}",
            normalized_url=f"https://mutu.example.com/article-{sequence}",
            title=f"Artikel mutu {sequence}",
            content_text="Pelaporan kasus DBD di Kota Mutu.",
            content_hash=f"{sequence:064x}",
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        now = timezone.now()
        Article.objects.filter(pk=article.pk).update(
            published_at=now - timedelta(hours=5),
            crawled_at=now - timedelta(hours=2),
        )
        article.refresh_from_db()
        return article

    def _signal(self, sequence, status=Signal.Status.NEEDS_REVIEW):
        return Signal.objects.create(
            code=f"SIG-Q-{sequence:04d}",
            title=f"Sinyal mutu {sequence}",
            summary="Sinyal untuk pengujian pusat mutu.",
            primary_disease=self.disease,
            primary_location=self.location,
            status=status,
        )

    def _query(self):
        return f"?date_from={self.today}&date_to={self.today}"

    def test_workspace_is_readable_by_viewer_but_mutation_is_forbidden(self):
        self.client.force_login(self.viewer)
        url = reverse("dashboard:quality-evaluation")
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"action": "create_snapshot"})
        self.assertEqual(response.status_code, 403)

    def test_metrics_use_analyst_review_outcomes_and_signal_decisions(self):
        article_one = self._article(1)
        article_two = self._article(2)
        article_three = self._article(3)
        now = timezone.now()
        for article, status in (
            (article_one, ValidationStatus.VALIDATED),
            (article_two, ValidationStatus.CORRECTED),
            (article_three, ValidationStatus.REJECTED),
        ):
            ArticleDisease.objects.create(
                article=article,
                disease=self.disease,
                validation_status=status,
                validated_by=self.analyst,
                validated_at=now,
            )
        ArticleLocation.objects.create(
            article=article_one,
            location=self.location,
            validation_status=ValidationStatus.VALIDATED,
            validated_by=self.analyst,
            validated_at=now,
        )
        ArticleFact.objects.create(
            article=article_one,
            disease=self.disease,
            location=self.location,
            validation_status=ValidationStatus.CORRECTED,
            validated_by=self.analyst,
            validated_at=now,
        )
        accepted = self._signal(1)
        rejected = self._signal(2)
        SignalHistory.objects.create(
            signal=accepted,
            from_status=Signal.Status.UNDER_REVIEW,
            to_status=Signal.Status.VALIDATED,
            changed_by=self.reviewer,
        )
        SignalHistory.objects.create(
            signal=rejected,
            from_status=Signal.Status.UNDER_REVIEW,
            to_status=Signal.Status.REJECTED,
            changed_by=self.reviewer,
        )

        report = build_quality_report(self.today, self.today)
        metrics = report["metric_map"]
        self.assertEqual(metrics["disease_exact_rate"]["value"], 33.3)
        self.assertEqual(metrics["disease_correction_rate"]["value"], 33.3)
        self.assertEqual(metrics["location_exact_rate"]["value"], 100.0)
        self.assertEqual(metrics["fact_correction_rate"]["value"], 100.0)
        self.assertEqual(metrics["signal_validity_rate"]["value"], 50.0)

    def test_collection_funnel_and_publication_latency_are_computed(self):
        article = self._article(11)
        job = CollectionJob.objects.create(
            source=self.source,
            job_type=CollectionJob.JobType.CRAWLER,
            status=CollectionJob.Status.COMPLETED,
        )
        CollectionJobItem.objects.create(
            collection_job=job,
            article=article,
            original_url=article.original_url,
            normalized_url=article.normalized_url,
            status=CollectionJobItem.Status.CREATED,
        )
        CollectionJobItem.objects.create(
            collection_job=job,
            original_url="https://mutu.example.com/duplicate",
            status=CollectionJobItem.Status.DUPLICATE,
        )
        ArticleValidationAssessment.objects.create(
            article=article,
            validation_status=ArticleValidationAssessment.ValidationStatus.VALIDATED,
            source_reliability=ArticleValidationAssessment.SourceReliability.B,
            information_credibility=2,
            evaluated_by=self.analyst,
        )
        report = build_quality_report(self.today, self.today)
        metrics = report["metric_map"]
        self.assertEqual(metrics["collection_candidates"]["value"], 2)
        self.assertEqual(metrics["collection_yield_rate"]["value"], 50.0)
        self.assertEqual(metrics["collection_duplicate_rate"]["value"], 50.0)
        self.assertEqual(metrics["publication_to_collection_hours"]["value"], 3.0)
        self.assertEqual(metrics["article_validity_rate"]["value"], 100.0)

    def test_expert_validation_can_be_created_from_workspace(self):
        self.client.force_login(self.analyst)
        payload = {
            "action": "create_evaluation",
            "evaluation_type": EvaluationRecord.EvaluationType.EXPERT,
            "evaluator_name": "Dr. Ahli MedIntel",
            "evaluator_role": "Ahli Intelijen Medik",
            "institution": "STIN",
            "evaluation_date": self.today,
            "general_findings": "Alur sistem sesuai kebutuhan operasional.",
            "recommendations": "Perkuat uji pada data nyata.",
        }
        for criterion in criteria_for(EvaluationRecord.EvaluationType.EXPERT):
            payload[f"score__{criterion['code']}"] = "4"
            payload[f"note__{criterion['code']}"] = "Diperiksa ahli."
        response = self.client.post(
            reverse("dashboard:quality-evaluation") + f"{self._query()}&new=expert",
            payload,
        )
        self.assertEqual(response.status_code, 302)
        evaluation = EvaluationRecord.objects.get()
        self.assertTrue(evaluation.code.startswith(f"VA-{self.today.year}-"))
        self.assertEqual(evaluation.average_score, 4.0)
        self.assertEqual(evaluation.status, EvaluationRecord.Status.DRAFT)
        self.assertEqual(evaluation.history.count(), 1)

    def test_uat_requires_all_scores_tasks_and_findings_before_completion(self):
        evaluation = EvaluationRecord.objects.create(
            code=f"UAT-{self.today.year}-0001",
            evaluation_type=EvaluationRecord.EvaluationType.UAT,
            evaluator_name="Pengguna Uji",
            evaluator_role="Analis",
            evaluation_date=self.today,
            scores={},
            task_results={},
            created_by=self.analyst,
        )
        blockers = completion_blockers(evaluation)
        self.assertEqual(len(blockers), 3)

    def test_only_reviewer_can_complete_and_completed_result_is_locked(self):
        scores = {
            criterion["code"]: 4
            for criterion in criteria_for(EvaluationRecord.EvaluationType.UAT)
        }
        tasks = {code: "passed" for code, _label in UAT_TASKS}
        evaluation = EvaluationRecord.objects.create(
            code=f"UAT-{self.today.year}-0002",
            evaluation_type=EvaluationRecord.EvaluationType.UAT,
            evaluator_name="Pengguna Uji",
            evaluator_role="Analis",
            evaluation_date=self.today,
            scores=scores,
            task_results=tasks,
            general_findings="Semua skenario berhasil dijalankan.",
            created_by=self.analyst,
        )
        url = reverse("dashboard:quality-evaluation") + (
            f"{self._query()}&evaluation={evaluation.pk}&tab=human"
        )
        self.client.force_login(self.analyst)
        denied = self.client.post(
            url,
            {
                "action": "complete_evaluation",
                "evaluation_id": evaluation.pk,
                "decision_notes": "Disahkan.",
            },
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.reviewer)
        completed = self.client.post(
            url,
            {
                "action": "complete_evaluation",
                "evaluation_id": evaluation.pk,
                "decision_notes": "Bukti lengkap dan disahkan reviewer.",
            },
        )
        self.assertEqual(completed.status_code, 302)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, EvaluationRecord.Status.COMPLETED)
        self.assertIsNotNone(evaluation.completed_at)

    def test_snapshot_freezes_report_for_selected_period(self):
        self.client.force_login(self.analyst)
        response = self.client.post(
            reverse("dashboard:quality-evaluation") + self._query(),
            {
                "action": "create_snapshot",
                "snapshot_title": "Snapshot Uji Mutu",
            },
        )
        self.assertEqual(response.status_code, 302)
        snapshot = QualityMetricSnapshot.objects.get()
        self.assertEqual(snapshot.period_start, self.today)
        self.assertIn("sections", snapshot.metrics)
        self.assertIn("Pada periode", snapshot.narrative)

    def test_dataset_export_contains_traceable_tables_and_bab4_summary(self):
        self.client.force_login(self.viewer)
        response = self.client.get(
            reverse("dashboard:quality-dataset-export") + self._query()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "01_ringkasan_metrik.csv",
                    "02_validasi_artikel.csv",
                    "03_review_ekstraksi.csv",
                    "04_keputusan_sinyal.csv",
                    "05_timeline_peringatan.csv",
                    "06_validasi_ahli_uat.csv",
                    "07_skenario_uat.csv",
                    "08_ringkasan_bab_iv.txt",
                },
            )
            narrative = archive.read("08_ringkasan_bab_iv.txt").decode("utf-8")
            self.assertIn("Validasi manusia", narrative)
