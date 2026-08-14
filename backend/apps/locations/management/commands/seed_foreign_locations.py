from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.locations.geolocation import clear_geolocation_cache
from apps.locations.models import Location, LocationAlias


COUNTRIES = (
    ("US", "United States", 39.828300, -98.579500,
     ("USA", "U.S.A.", "United States of America", "Amerika Serikat")),
    ("CN", "China", 35.861700, 104.195400, ("Tiongkok", "PRC")),
    ("IN", "India", 20.593700, 78.962900, ()),
    ("MY", "Malaysia", 4.210500, 101.975800, ()),
    ("SG", "Singapore", 1.352100, 103.819800, ("Singapura",)),
    ("PH", "Philippines", 12.879700, 121.774000, ("Filipina",)),
    ("AU", "Australia", -25.274400, 133.775100, ()),
    ("GB", "United Kingdom", 55.378100, -3.436000,
     ("Britania Raya", "Inggris", "U.K.")),
    ("CA", "Canada", 56.130400, -106.346800, ("Kanada",)),
    ("JP", "Japan", 36.204800, 138.252900, ("Jepang",)),
    ("KR", "South Korea", 35.907800, 127.766900,
     ("Korea Selatan", "Republic of Korea")),
    ("SA", "Saudi Arabia", 23.885900, 45.079200, ("Arab Saudi",)),
    ("AE", "United Arab Emirates", 23.424100, 53.847800, ("UAE",)),
    ("BR", "Brazil", -14.235000, -51.925300, ("Brasil",)),
    ("CD", "Democratic Republic of the Congo", -4.038300, 21.758700,
     ("DR Congo", "DRC", "Republik Demokratik Kongo")),
    ("TH", "Thailand", 15.870000, 100.992500, ()),
    ("VN", "Vietnam", 14.058300, 108.277200, ()),
    ("DE", "Germany", 51.165700, 10.451500, ("Jerman",)),
    ("FR", "France", 46.227600, 2.213700, ("Prancis",)),
    ("ZA", "South Africa", -30.559500, 22.937500, ("Afrika Selatan",)),
)


US_REGIONS = (
    ("US-CO", "Colorado", 39.550100, -105.782100),
    ("US-MI", "Michigan", 44.314800, -85.602400),
    ("US-CA", "California", 36.778300, -119.417900),
    ("US-NY", "New York", 43.000000, -75.000000),
    ("US-TX", "Texas", 31.000000, -100.000000),
    ("US-FL", "Florida", 27.664800, -81.515800),
    ("US-WA", "Washington", 47.400900, -120.740100),
    ("US-HI", "Hawaii", 19.896800, -155.582800),
    ("US-AK", "Alaska", 64.200800, -149.493700),
)


class Command(BaseCommand):
    help = (
        "Menambahkan master awal negara dan wilayah luar negeri untuk "
        "resolver artikel OSINT global. Aman dijalankan berulang kali."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        created_locations = 0
        updated_locations = 0
        created_aliases = 0
        countries = {}

        for code, name, latitude, longitude, aliases in COUNTRIES:
            location = Location.objects.filter(
                name=name,
                administrative_level=Location.AdministrativeLevel.COUNTRY,
                parent__isnull=True,
                country_code=code,
            ).first()

            if location is None:
                location = Location.objects.create(
                    name=name,
                    code=code,
                    administrative_level=Location.AdministrativeLevel.COUNTRY,
                    country_code=code,
                    latitude=latitude,
                    longitude=longitude,
                    is_active=True,
                )
                created_locations += 1
            else:
                changed = False
                for field, value in (
                    ("code", code),
                    ("latitude", Decimal(str(latitude))),
                    ("longitude", Decimal(str(longitude))),
                    ("is_active", True),
                ):
                    if getattr(location, field) != value:
                        setattr(location, field, value)
                        changed = True
                if changed:
                    location.save()
                    updated_locations += 1

            countries[code] = location
            for alias in aliases:
                _, created = LocationAlias.objects.get_or_create(
                    location=location,
                    alias=alias,
                    defaults={"language": "id", "is_active": True},
                )
                created_aliases += int(created)

        united_states = countries["US"]
        for code, name, latitude, longitude in US_REGIONS:
            location = Location.objects.filter(
                name=name,
                administrative_level=Location.AdministrativeLevel.PROVINCE,
                parent=united_states,
                country_code="US",
            ).first()

            if location is None:
                Location.objects.create(
                    name=name,
                    code=code,
                    administrative_level=Location.AdministrativeLevel.PROVINCE,
                    parent=united_states,
                    country_code="US",
                    latitude=latitude,
                    longitude=longitude,
                    is_active=True,
                )
                created_locations += 1
            else:
                changed = False
                for field, value in (
                    ("code", code),
                    ("latitude", Decimal(str(latitude))),
                    ("longitude", Decimal(str(longitude))),
                    ("is_active", True),
                ):
                    if getattr(location, field) != value:
                        setattr(location, field, value)
                        changed = True
                if changed:
                    location.save()
                    updated_locations += 1

        clear_geolocation_cache()
        self.stdout.write(self.style.SUCCESS(
            "Master lokasi luar negeri siap. "
            f"Lokasi dibuat: {created_locations}; "
            f"lokasi diperbarui: {updated_locations}; "
            f"alias dibuat: {created_aliases}."
        ))
