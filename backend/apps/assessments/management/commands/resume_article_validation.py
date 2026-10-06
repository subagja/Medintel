from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.db import transaction
from apps.articles.models import Article
from apps.assessments.models import BulkArticleValidationJob as Job

class Command(BaseCommand):
    help = "Lanjutkan job gagal dari daftar kandidat tersimpan; jangan jalankan saat worker lama masih aktif."
    def add_arguments(self, parser):
        parser.add_argument("job_id")
    def handle(self, *args, **options):
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=options["job_id"])
            if job.status != Job.Status.FAILED:
                raise CommandError("Hanya job gagal/terputus yang dapat dilanjutkan.")
            summary = dict(job.summary or {})
            if not summary.get("candidate_ids"):
                raise CommandError("Job lama tidak memiliki daftar kandidat. Tidak ada perubahan.")
            if Job.objects.exclude(pk=job.pk).filter(status__in=[Job.Status.QUEUED, Job.Status.RUNNING]).exists():
                raise CommandError("Masih ada batch aktif. Tidak ada perubahan.")
            finished = set(summary.get("finished_ids", []))
            for article in Article.objects.select_for_update().filter(pk__in=summary["candidate_ids"]):
                metadata = dict(article.raw_metadata or {})
                if metadata.get("validation_process_job_id") != str(job.pk): continue
                # Hasil tersimpan sebelum checkpoint job: jangan dinilai ulang.
                if metadata.get("validation_process_state") == "done":
                    finished.add(str(article.pk))
                elif metadata.get("validation_process_state") == "running":
                    metadata["validation_process_state"] = "failed" if summary.get("mode") == "failed" else "done" if summary.get("mode") in ("done", "updated") else "new"
                    article.raw_metadata = metadata
                    article.save(update_fields=["raw_metadata", "updated_at"])
            summary["finished_ids"] = list(finished)
            job.summary = summary
            job.processed_items = len(finished)
            job.status = Job.Status.QUEUED
            job.completed_at = None
            job.error_message = ""
            job.save(update_fields=["summary", "processed_items", "status", "completed_at", "error_message", "updated_at"])
        try:
            call_command("bulk_validate_articles", job_id=str(job.pk), limit=job.batch_size)
        except Exception as exc:
            from django.utils import timezone
            Job.objects.filter(pk=job.pk).update(status=Job.Status.FAILED, error_message=str(exc)[:2000], updated_at=timezone.now())
            raise
