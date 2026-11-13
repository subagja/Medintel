"""Persistent MEDINTEL job worker; launch in its own terminal."""
import time
from django.core.management.base import BaseCommand, CommandError
from apps.assessments.services.integrated_processing import run_next_job, ready_for_ai

class Command(BaseCommand):
    help = 'Proses batch klasifikasi + ekstraksi terpadu yang diotorisasi Superuser.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Ambil satu job lalu keluar')
        parser.add_argument('--interval', type=int, default=5, help='Interval polling detik (>=2)')
        parser.add_argument('--recover-stale', action='store_true', help='Pulihkan job yang terputus >15 menit')

    def handle(self, *args, **options):
        if options['interval'] < 2:
            raise CommandError('Interval minimal 2 detik')
        missing = ready_for_ai()
        if missing:
            raise CommandError('AI belum siap di terminal worker: ' + ', '.join(missing))
        self.stdout.write('Worker pengolahan terpadu aktif. Tekan Ctrl+C untuk menghentikan.')
        try:
            while True:
                job = run_next_job(recover_stale=options['recover_stale'])
                if job:
                    self.stdout.write('Job %s: %s %s/%s; %s' % (
                        job.pk, job.status, job.processed_items, job.total_items, job.stats))
                if options['once']:
                    break
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Worker dihentikan.')
