from __future__ import annotations

import os
import socket
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.collection.models import CollectionWorker
from apps.collection.services.queue import (
    claim_next_collection_job,
    recover_stale_collection_jobs,
)
from apps.collection.services.scheduling import enqueue_due_collection_schedules
from apps.collection.services.worker import execute_collection_job


class Command(BaseCommand):
    help = (
        "Jalankan worker antrean koleksi persisten. Worker terpisah dari "
        "runserver sehingga job tetap aman saat halaman atau server web ditutup."
    )

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=3)
        parser.add_argument("--poll-interval", type=float, default=2.0)
        parser.add_argument("--lease-seconds", type=int, default=180)
        parser.add_argument(
            "--once",
            action="store_true",
            help="Proses antrean yang tersedia lalu berhenti.",
        )
        parser.add_argument(
            "--skip-schedules",
            action="store_true",
            help="Jangan membentuk sesi dari jadwal jatuh tempo.",
        )

    def handle(self, *args, **options):
        concurrency = min(max(options["concurrency"], 1), 10)
        poll_interval = min(max(options["poll_interval"], 0.2), 30.0)
        lease_seconds = min(max(options["lease_seconds"], 30), 3600)
        once = options["once"]
        worker_id = (
            f"{socket.gethostname()}:{os.getpid()}:"
            f"{uuid.uuid4().hex[:8]}"
        )
        worker, _ = CollectionWorker.objects.update_or_create(
            id=worker_id,
            defaults={
                "hostname": socket.gethostname(),
                "process_id": os.getpid(),
                "status": CollectionWorker.Status.ACTIVE,
                "concurrency": concurrency,
                "started_at": timezone.now(),
                "last_heartbeat_at": timezone.now(),
                "stopped_at": None,
                "metadata": {"lease_seconds": lease_seconds},
            },
        )
        recovery = recover_stale_collection_jobs()
        self.stdout.write(
            self.style.SUCCESS(
                f"Worker aktif {worker_id} · concurrency={concurrency} · "
                f"pemulihan={recovery['recovered']}."
            )
        )

        futures = set()
        last_housekeeping = None
        executor = ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="collection-worker",
        )
        try:
            while True:
                now = timezone.now()
                CollectionWorker.objects.filter(pk=worker.pk).update(
                    status=CollectionWorker.Status.ACTIVE,
                    last_heartbeat_at=now,
                )
                if (
                    last_housekeeping is None
                    or (now - last_housekeeping).total_seconds() >= 20
                ):
                    recover_stale_collection_jobs(now=now)
                    if not options["skip_schedules"]:
                        enqueue_due_collection_schedules(now=now)
                    last_housekeeping = now

                futures = {future for future in futures if not future.done()}
                claimed_any = False
                while len(futures) < concurrency:
                    close_old_connections()
                    job = claim_next_collection_job(
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                    )
                    if job is None:
                        break
                    futures.add(executor.submit(execute_collection_job, job))
                    claimed_any = True
                    self.stdout.write(
                        f"Menjalankan job {job.id} · {job.queue_key} · "
                        f"percobaan {job.attempt_count}/{job.max_attempts}"
                    )

                if once and not futures and not claimed_any:
                    break
                if futures:
                    wait(
                        futures,
                        timeout=poll_interval,
                        return_when=FIRST_COMPLETED,
                    )
                else:
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Worker dihentikan pengguna."))
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
            CollectionWorker.objects.filter(pk=worker.pk).update(
                status=CollectionWorker.Status.STOPPED,
                stopped_at=timezone.now(),
                last_heartbeat_at=timezone.now(),
            )
            close_old_connections()
            self.stdout.write(self.style.SUCCESS("Worker berhenti dengan aman."))
