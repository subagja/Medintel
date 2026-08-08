"""Tandai CollectionJob yang macet di status RUNNING sebagai FAILED.

Skenario paling umum: background thread crawler mati di tengah jalan
(mis. server dev di-restart/auto-reload saat crawl masih berjalan),
sehingga job-nya tidak pernah sempat dipanggil complete_collection_job
atau fail_collection_job -- tertinggal RUNNING selamanya.

Command ini aman dijalankan berulang kali (idempotent): job yang
memang masih benar-benar berjalan (dalam ambang waktu wajar) tidak
disentuh.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.collection.models import CollectionJob


class Command(BaseCommand):
    help = (
        "Tandai job crawler yang macet di status RUNNING lebih lama "
        "dari ambang waktu tertentu sebagai FAILED."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=30,
            help=(
                "Ambang waktu (menit) sejak started_at -- job RUNNING "
                "yang lebih tua dari ini dianggap macet (default: 30)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Cuma tampilkan job yang akan ditandai, tanpa mengubah apapun.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(
            minutes=options["minutes"]
        )

        stale_jobs = CollectionJob.objects.filter(
            status=CollectionJob.Status.RUNNING,
            started_at__lt=cutoff,
        )

        count = stale_jobs.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS("Tidak ada job yang macet.")
            )
            return

        for job in stale_jobs:
            self.stdout.write(
                f"  - {job.source.code} (mulai {job.started_at}, "
                f"job_id={job.id})"
            )

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: {count} job akan ditandai FAILED "
                    "(tidak ada perubahan disimpan)."
                )
            )
            return

        updated = stale_jobs.update(
            status=CollectionJob.Status.FAILED,
            finished_at=timezone.now(),
            error_message=(
                "Job ditandai gagal otomatis: macet di status RUNNING "
                f"lebih dari {options['minutes']} menit, kemungkinan "
                "proses server terhenti/di-restart di tengah crawl."
            ),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{updated} job macet berhasil ditandai FAILED."
            )
        )
