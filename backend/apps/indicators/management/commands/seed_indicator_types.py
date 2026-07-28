from django.core.management.base import BaseCommand

from apps.indicators.models import IndicatorType


INDICATOR_TYPES = [
    {
        "code": "case-increase",
        "name": "Peningkatan Kasus",
        "description": (
            "Indikasi adanya peningkatan jumlah kasus penyakit."
        ),
        "category": IndicatorType.Category.EPIDEMIOLOGICAL,
        "default_weight": 2.0,
    },
    {
        "code": "death-reported",
        "name": "Kematian Dilaporkan",
        "description": (
            "Indikasi adanya kematian yang berkaitan dengan penyakit."
        ),
        "category": IndicatorType.Category.IMPACT,
        "default_weight": 3.0,
    },
    {
        "code": "new-occurrence",
        "name": "Kejadian Baru",
        "description": (
            "Indikasi kejadian baru atau kasus pertama di suatu wilayah."
        ),
        "category": IndicatorType.Category.EPIDEMIOLOGICAL,
        "default_weight": 2.5,
    },
    {
        "code": "geographic-spread",
        "name": "Penyebaran Geografis",
        "description": (
            "Indikasi perluasan kejadian ke wilayah lain."
        ),
        "category": IndicatorType.Category.GEOGRAPHIC,
        "default_weight": 2.5,
    },
    {
        "code": "government-response",
        "name": "Respons Pemerintah",
        "description": (
            "Indikasi adanya tindakan atau respons dari pemerintah."
        ),
        "category": IndicatorType.Category.RESPONSE,
        "default_weight": 1.0,
    },
]


class Command(BaseCommand):
    help = "Mengisi master awal tipe indikator MedIntel."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for data in INDICATOR_TYPES:
            indicator_type, created = (
                IndicatorType.objects.update_or_create(
                    code=data["code"],
                    defaults=data,
                )
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

            self.stdout.write(
                f"{indicator_type.code}: "
                f"{'created' if created else 'updated'}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Master tipe indikator selesai | "
                    f"dibuat={created_count} | "
                    f"diperbarui={updated_count}"
                )
            )
        )