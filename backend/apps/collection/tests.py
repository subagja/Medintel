from django.test import TestCase

from apps.sources.models import Source

from .models import CollectionJob
from .services import (
    complete_collection_job,
    fail_collection_job,
    start_collection_job,
)


class CollectionJobServiceTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Collection Uji",
            code="media-collection-uji",
            domain="example.com",
            base_url="https://example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

    def test_complete_job_updates_totals_and_source_timestamp(self):
        job = start_collection_job(
            source=self.source,
            job_type=CollectionJob.JobType.CRAWLER,
            trigger_type="test",
        )

        complete_collection_job(
            job=job,
            total_found=4,
            total_created=1,
            total_duplicate=1,
            total_rejected=2,
            total_failed=0,
        )

        job.refresh_from_db()
        self.source.refresh_from_db()

        self.assertEqual(
            job.status,
            CollectionJob.Status.COMPLETED,
        )
        self.assertEqual(job.total_found, 4)
        self.assertIsNotNone(self.source.last_crawled_at)

    def test_failed_job_preserves_partial_totals(self):
        job = start_collection_job(
            source=self.source,
            job_type=CollectionJob.JobType.CRAWLER,
            trigger_type="test",
        )

        fail_collection_job(
            job=job,
            error_message="Koneksi terputus.",
            total_found=3,
            total_created=1,
            total_duplicate=0,
            total_rejected=1,
            total_failed=1,
        )

        job.refresh_from_db()

        self.assertEqual(
            job.status,
            CollectionJob.Status.FAILED,
        )
        self.assertEqual(job.total_found, 3)
        self.assertEqual(job.total_created, 1)
        self.assertEqual(job.total_rejected, 1)
        self.assertEqual(job.total_failed, 1)
