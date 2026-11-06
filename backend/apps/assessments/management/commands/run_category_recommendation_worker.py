"""Worker ringan untuk pra-hitung rekomendasi kategori AI pada artikel baru.

Contoh:
  python manage.py run_category_recommendation_worker --once --batch-size 1
  python manage.py run_category_recommendation_worker --interval 90 --batch-size 2 --max-total 50
"""
import importlib.util
import os
import time

from django.core.management.base import BaseCommand, CommandError
from apps.assessments.services.auto_category_recommendations import generate_available


class Command(BaseCommand):
    help = 'Pra-hitung rekomendasi jenis informasi untuk artikel tanpa klasifikasi terkonfirmasi'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Satu putaran lalu keluar')
        parser.add_argument('--batch-size', type=int, default=2, help='Maksimal artikel per putaran (1-20)')
        parser.add_argument('--interval', type=int, default=90, help='Detik antar putaran (minimal 20)')
        parser.add_argument('--max-total', type=int, default=50, help='Batas panggilan AI per sesi worker (1-500)')
        parser.add_argument('--retry-failed', action='store_true', help='Coba ulang error lama secara eksplisit')

    def handle(self, *args, **opts):
        if not os.environ.get('OPENAI_API_KEY'):
            raise CommandError('OPENAI_API_KEY belum tersedia pada terminal worker')
        if importlib.util.find_spec('openai') is None:
            raise CommandError('Paket openai belum terpasang pada Python aktif')
        size = opts['batch_size']
        interval = opts['interval']
        max_total = opts['max_total']
        if not 1 <= size <= 20 or interval < 20 or not 1 <= max_total <= 500:
            raise CommandError('Batas: batch-size 1-20, interval >=20, max-total 1-500')
        used = 0
        self.stdout.write('Worker rekomendasi kategori aktif. Tidak melakukan approval otomatis.')
        try:
            while used < max_total:
                result = generate_available(min(size, max_total - used), retry_failed=opts['retry_failed'])
                used += result['generated'] + result['failed']
                self.stdout.write('Hasil: %s | penggunaan sesi %d/%d' % (result, used, max_total))
                if opts['once']:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('Worker dihentikan secara normal.')
