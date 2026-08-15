from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.sources.models import Source

from .forms import CollectionScheduleForm
from .models import (
    CollectionJob,
    CollectionSchedule,
    CollectionSession,
)
from .services.queue import (
    claim_next_collection_job,
    enqueue_collection_job,
    mark_collection_job_interrupted,
    recover_stale_collection_jobs,
    request_collection_job_cancel,
    request_collection_job_pause,
    rerun_collection_job,
    resume_collection_job,
    schedule_collection_job_retry,
)
from .services.scheduling import (
    calculate_next_run_at,
    enqueue_due_collection_schedules,
    run_collection_schedule,
)


class PersistentCollectionQueueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Antrean Uji",
            code="media-antrean-uji",
            domain="queue.example.com",
            base_url="https://queue.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
        )

    def enqueue(self, **overrides):
        values = {
            "source": self.source,
            "job_type": CollectionJob.JobType.CRAWLER,
            "crawler_name": "GenericHtmlCrawler",
            "trigger_type": "test",
        }
        values.update(overrides)
        return enqueue_collection_job(**values)

    def test_job_is_persisted_before_worker_claims_it(self):
        job = self.enqueue()
        self.assertEqual(job.status, CollectionJob.Status.PENDING)
        self.assertTrue(job.queue_key)
        self.assertEqual(job.attempt_count, 0)
        self.assertEqual(job.logs.first().event, "queued")

        claimed = claim_next_collection_job(worker_id="worker-test")
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(claimed.status, CollectionJob.Status.RUNNING)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.worker_id, "worker-test")
        self.assertIsNotNone(claimed.lease_expires_at)

    def test_same_source_channel_cannot_run_concurrently(self):
        first = self.enqueue()
        self.enqueue()
        claimed = claim_next_collection_job(worker_id="worker-a")
        self.assertEqual(claimed.id, first.id)
        self.assertIsNone(claim_next_collection_job(worker_id="worker-b"))

    def test_pending_job_can_be_paused_and_resumed(self):
        job = self.enqueue()
        request_collection_job_pause(job=job)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.PAUSED)

        resume_collection_job(job=job)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.PENDING)
        self.assertIsNone(job.pause_requested_at)

    def test_running_cancel_is_cooperative_then_terminal(self):
        job = self.enqueue()
        job = claim_next_collection_job(worker_id="worker-test")
        request_collection_job_cancel(job=job)
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.RUNNING)
        self.assertIsNotNone(job.cancel_requested_at)

        mark_collection_job_interrupted(
            job=job,
            status=CollectionJob.Status.CANCELLED,
            totals={"total_found": 2},
            message="Checkpoint pembatalan.",
        )
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.CANCELLED)
        self.assertEqual(job.total_found, 2)

    @override_settings(COLLECTION_RETRY_BASE_SECONDS=1)
    def test_transient_failure_is_scheduled_with_backoff(self):
        job = self.enqueue(max_attempts=3)
        job = claim_next_collection_job(worker_id="worker-test")
        self.assertTrue(
            schedule_collection_job_retry(
                job=job,
                error_message="Connection timeout",
            )
        )
        job.refresh_from_db()
        self.assertEqual(job.status, CollectionJob.Status.RETRY_WAITING)
        self.assertGreater(job.available_at, timezone.now())

    @override_settings(COLLECTION_RETRY_BASE_SECONDS=1)
    def test_expired_worker_lease_is_recovered(self):
        job = self.enqueue(max_attempts=3)
        job = claim_next_collection_job(worker_id="dead-worker")
        CollectionJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        result = recover_stale_collection_jobs()
        job.refresh_from_db()
        self.assertEqual(result["recovered"], 1)
        self.assertEqual(job.status, CollectionJob.Status.RETRY_WAITING)
        self.assertEqual(job.worker_id, "")

    def test_rerun_preserves_lineage_and_session(self):
        session = CollectionSession.objects.create(
            scope=CollectionSession.Scope.SINGLE_SOURCE,
            selected_source=self.source,
            planned_job_count=1,
        )
        job = self.enqueue(session=session)
        job.status = CollectionJob.Status.COMPLETED
        job.save(update_fields=["status"])
        rerun = rerun_collection_job(job=job)
        session.refresh_from_db()
        self.assertEqual(rerun.rerun_of_id, job.id)
        self.assertEqual(rerun.session_id, session.id)
        self.assertEqual(session.planned_job_count, 2)


class CollectionScheduleTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Jadwal Uji",
            code="media-jadwal-uji",
            domain="schedule.example.com",
            base_url="https://schedule.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=True,
        )
        self.user = get_user_model().objects.create_user(
            username="schedule-analyst",
            password="test-password",
        )
        analyst, _ = Group.objects.get_or_create(name="Analyst")
        self.user.groups.add(analyst)

    def schedule(self, **overrides):
        values = {
            "name": "Koleksi Harian Media Uji",
            "source": self.source,
            "recurrence": CollectionSchedule.Recurrence.DAILY,
            "run_time": time(6, 0),
            "next_run_at": timezone.now() - timedelta(minutes=1),
            "created_by": self.user,
        }
        values.update(overrides)
        return CollectionSchedule.objects.create(**values)

    def test_daily_next_run_is_in_the_future(self):
        schedule = self.schedule()
        next_run = calculate_next_run_at(schedule, after=timezone.now())
        self.assertGreater(next_run, timezone.now())

    def test_form_creates_one_schedule_for_all_sources_without_requirement(self):
        form = CollectionScheduleForm(
            data={
                "name": "Koleksi Harian Semua Sumber",
                "source": CollectionSession.Scope.ALL_READY,
                "recurrence": CollectionSchedule.Recurrence.DAILY,
                "run_time": "06:00",
                "weekday": "0",
                "max_attempts": "3",
                "intelligence_requirement": "",
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        schedule = form.save()
        self.assertIsNone(schedule.source_id)
        self.assertEqual(
            schedule.source_scope,
            CollectionSession.Scope.ALL_READY,
        )
        self.assertIsNone(schedule.intelligence_requirement_id)

    @patch("apps.crawlers.unified.start_unified_collection")
    def test_all_source_schedule_starts_one_unified_session(self, start_mock):
        session = CollectionSession.objects.create()
        start_mock.return_value = session
        schedule = self.schedule(
            source=None,
            source_scope=CollectionSession.Scope.ALL_READY,
        )

        result = run_collection_schedule(schedule)

        self.assertEqual(result, session)
        self.assertEqual(
            start_mock.call_args.kwargs["source_code"],
            CollectionSession.Scope.ALL_READY,
        )

    @patch("apps.crawlers.unified.start_unified_collection")
    def test_due_schedule_creates_session_and_advances_time(self, start_mock):
        session = CollectionSession.objects.create()
        start_mock.return_value = session
        schedule = self.schedule()
        result = enqueue_due_collection_schedules()
        schedule.refresh_from_db()
        self.assertEqual(result["scheduled"], [str(session.id)])
        self.assertEqual(schedule.last_session_id, session.id)
        self.assertGreater(schedule.next_run_at, timezone.now())

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": (
                    "django.contrib.staticfiles.storage.StaticFilesStorage"
                ),
            },
        }
    )
    def test_schedule_workspace_is_readable_and_mutation_is_role_guarded(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:crawler-schedule-workspace"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Penjadwalan dan Worker Koleksi")
        self.assertContains(response, "run_collection_worker")
        self.assertContains(response, "Semua sumber aktif dan siap")
        self.assertContains(response, "Semua kebutuhan / koleksi rutin")

        viewer = get_user_model().objects.create_user(username="queue-viewer")
        viewer_group, _ = Group.objects.get_or_create(name="Viewer")
        viewer.groups.add(viewer_group)
        self.client.force_login(viewer)
        response = self.client.post(
            reverse("dashboard:crawler-schedule-workspace"),
            {"action": "create"},
        )
        self.assertEqual(response.status_code, 403)
