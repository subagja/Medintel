import re
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from intel.models import PublisherDomainAlias


def normalize_source_alias(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\s\.\-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()

    if not value:
        return ""

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.netloc or value

    value = value.replace("www.", "", 1)
    value = value.strip("/")

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
            ("Radar Pacitan", "radarmadiun.jawapos.com"),
            ("radarmadiun.jawapos.com", "radarmadiun.jawapos.com"),
            ("Jawa Pos Radar Madiun", "radarmadiun.jawapos.com"),

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
            ("serumpun.babelprov.go.id", "serumpun.babelprov.go.id"),

            ("Berita Cilegon", "berita.cilegon.go.id"),
            ("Pemerintah Kota Cilegon", "berita.cilegon.go.id"),
            ("berita.cilegon.go.id", "berita.cilegon.go.id"),
        ]

        created = 0
        updated = 0
        skipped = 0

        for alias, domain in rows:
            alias = (alias or "").strip()
            domain = normalize_domain(domain)

            if not alias or not domain:
                skipped += 1
                continue

            _, was_created = PublisherDomainAlias.objects.update_or_create(
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
            f"Publisher aliases seeded. Created={created}, Updated={updated}, Skipped={skipped}"
        ))