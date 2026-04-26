import re

from django.core.management.base import BaseCommand
from intel.models import PublisherDomainAlias


def normalize_source_alias(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s\.\-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


class Command(BaseCommand):
    help = "Seed alias domain publisher untuk URL resolution."

    def handle(self, *args, **options):
        rows = [
            ("RRI", "rri.co.id"),
            ("RRI.co.id", "rri.co.id"),
            ("Kompas", "kompas.com"),
            ("Kompas.com", "kompas.com"),
            ("ANTARA", "antaranews.com"),
            ("ANTARA News", "antaranews.com"),
            ("Detik", "detik.com"),
            ("detikcom", "detik.com"),
            ("Tribunnews", "tribunnews.com"),
            ("CNN Indonesia", "cnnindonesia.com"),
            ("Tempo", "tempo.co"),
            ("Liputan6", "liputan6.com"),

            ("Radar Madiun", "radarmadiun.jawapos.com"),
            ("radarmadiun.jawapos.com", "radarmadiun.jawapos.com"),
            ("Radar Kepahiang", "radarkepahiang.disway.id"),
            ("radarkepahiang.disway.id", "radarkepahiang.disway.id"),
            ("Disway", "disway.id"),

            ("Kalimantan Post", "kalimantanpost.com"),
            ("Sabang Merauke NEWS", "sabangmeraukenews.com"),
            ("Sabang Merauke", "sabangmeraukenews.com"),

            ("Pemerintah Provinsi Kepulauan Bangka Belitung", "serumpun.babelprov.go.id"),
            ("Pemprov Babel", "serumpun.babelprov.go.id"),
            ("Serumpun Babel", "serumpun.babelprov.go.id"),
            ("Babelprov", "serumpun.babelprov.go.id"),
        ]

        created = 0
        updated = 0

        for alias, domain in rows:
            obj, was_created = PublisherDomainAlias.objects.update_or_create(
                normalized_alias=normalize_source_alias(alias),
                defaults={
                    "alias": alias,
                    "domain": domain,
                    "is_active": True,
                },
            )

            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Publisher aliases seeded. Created={created}, Updated={updated}"
        ))