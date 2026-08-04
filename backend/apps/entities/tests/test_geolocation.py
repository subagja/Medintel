from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.geolocation import (
    clear_geolocation_cache,
    resolve_indonesia_locations,
)
from apps.entities.models import (
    ArticleFact,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    Location,
    LocationAlias,
)
from apps.entities.services import extract_article_entities
from apps.sources.models import Source


class IndonesiaGeolocationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="Media Geolocation",
            code="media-geolocation",
            domain="geo.example.com",
            base_url="https://geo.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={
                "canonical_name": "Dengue",
                "code": "dbd-geolocation",
                "is_active": True,
            },
        )
        if not self.disease.is_active:
            self.disease.is_active = True
            self.disease.save(update_fields=["is_active"])

        DiseaseAlias.objects.get_or_create(
            disease=self.disease,
            alias="DBD",
        )
        self.indonesia = Location.objects.create(
            name="Indonesia",
            code="ID",
            administrative_level=(
                Location.AdministrativeLevel.COUNTRY
            ),
            country_code="ID",
        )
        self.jawa_barat = Location.objects.create(
            name="Jawa Barat",
            code="32",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=self.indonesia,
            latitude=Decimal("-6.914744"),
            longitude=Decimal("107.609810"),
            country_code="ID",
        )
        self.kabupaten_bandung = Location.objects.create(
            name="Kabupaten Bandung",
            code="32.04",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.jawa_barat,
            latitude=Decimal("-7.025203"),
            longitude=Decimal("107.519760"),
            country_code="ID",
        )
        self.kota_bandung = Location.objects.create(
            name="Kota Bandung",
            code="32.73",
            administrative_level=(
                Location.AdministrativeLevel.CITY
            ),
            parent=self.jawa_barat,
            latitude=Decimal("-6.917464"),
            longitude=Decimal("107.619125"),
            country_code="ID",
        )
        self.michigan = Location.objects.create(
            name="Michigan",
            code="US-MI",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            latitude=Decimal("44.314844"),
            longitude=Decimal("-85.602364"),
            country_code="US",
        )
        clear_geolocation_cache()

    def test_resolves_explicit_regency_as_primary(self):
        text = (
            "Dinas Kesehatan mencatat 42 kasus DBD di "
            "Kabupaten Bandung, Jawa Barat."
        )
        result = resolve_indonesia_locations(
            text,
            title_length=0,
        )

        self.assertEqual(result.scope, "domestic")
        self.assertEqual(
            result.primary.location_id,
            str(self.kabupaten_bandung.id),
        )
        self.assertTrue(result.primary.is_mappable)

    def test_keeps_unqualified_bandung_ambiguous(self):
        result = resolve_indonesia_locations(
            "Sebanyak 42 kasus DBD dilaporkan di Bandung.",
        )

        self.assertEqual(result.scope, "unresolved")
        self.assertEqual(result.mentions, ())
        self.assertIn("Bandung", result.ambiguous_terms)

    def test_does_not_map_foreign_location_as_indonesia(self):
        result = resolve_indonesia_locations(
            "Dua kematian dilaporkan akibat siklosporiasis di Michigan.",
        )

        self.assertEqual(result.scope, "unresolved")
        self.assertEqual(result.mentions, ())

    def test_entity_extraction_marks_one_domestic_primary(self):
        article = Article.objects.create(
            source=self.source,
            original_url="https://geo.example.com/dbd-bandung",
            normalized_url="https://geo.example.com/dbd-bandung",
            title="42 kasus DBD di Kabupaten Bandung",
            content_text=(
                "Kasus terjadi di Kabupaten Bandung, Jawa Barat. "
                "Pemerintah daerah melakukan pengendalian vektor."
            ),
            content_hash="9" * 64,
        )

        result = extract_article_entities(article)
        primary_relations = ArticleLocation.objects.filter(
            article=article,
            is_primary=True,
        )

        self.assertEqual(primary_relations.count(), 1)
        self.assertEqual(
            primary_relations.get().location,
            self.kabupaten_bandung,
        )
        self.assertEqual(
            result.geolocation_scope,
            "domestic",
        )
        self.assertEqual(
            result.geolocation_metadata()["primary_location"]["code"],
            "32.04",
        )


class IndonesiaLocationImportTests(TestCase):
    def test_imports_full_hierarchy_from_standard_csv(self):
        with TemporaryDirectory() as directory:
            csv_path = Path(directory) / "wilayah.csv"
            csv_path.write_text(
                "kode,nama,level,kode_induk,lat,lon\n"
                "32,Jawa Barat,provinsi,ID,-6.914744,107.609810\n"
                "32.04,Kabupaten Bandung,kabupaten,32,-7.025203,107.519760\n"
                "32.04.01,Cileunyi,kecamatan,32.04,,\n"
                "32.04.01.1001,Cibiru Wetan,desa,32.04.01,,\n",
                encoding="utf-8",
            )

            call_command(
                "import_indonesia_locations",
                str(csv_path),
                verbosity=0,
            )

        village = Location.objects.get(
            code="32.04.01.1001",
        )

        self.assertEqual(village.parent.code, "32.04.01")
        self.assertEqual(
            village.parent.parent.code,
            "32.04",
        )
        self.assertEqual(
            village.parent.parent.parent.code,
            "32",
        )
        self.assertTrue(
            self.kabupaten_alias_exists()
        )

        call_command(
            "audit_indonesia_geolocation",
            verbosity=0,
        )

    @staticmethod
    def kabupaten_alias_exists() -> bool:
        return Location.objects.filter(
            code="32.04",
            aliases__alias="Kab. Bandung",
            aliases__is_active=True,
        ).exists()


class IndonesiaCoreLocationSyncTests(TestCase):
    def test_sync_builds_complete_clean_core_master(self):
        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            verbosity=0,
        )

        active = Location.objects.filter(
            country_code="ID",
            is_active=True,
        )
        self.assertEqual(
            active.filter(
                administrative_level=Location.AdministrativeLevel.PROVINCE,
            ).count(),
            38,
        )
        self.assertEqual(
            active.filter(
                administrative_level=Location.AdministrativeLevel.REGENCY,
            ).count(),
            416,
        )
        self.assertEqual(
            active.filter(
                administrative_level=Location.AdministrativeLevel.CITY,
            ).count(),
            98,
        )
        self.assertTrue(
            active.filter(
                code="93.04",
                name="Kabupaten Asmat",
                parent__code="93",
            ).exists()
        )
        self.assertTrue(
            active.filter(
                code="31.01",
                name="Kabupaten Administrasi Kepulauan Seribu",
            ).exists()
        )
        call_command(
            "audit_indonesia_geolocation",
            strict=True,
            verbosity=0,
        )

    def test_sync_repairs_old_papua_code_and_wrong_level(self):
        indonesia = Location.objects.create(
            name="Indonesia",
            code="ID",
            administrative_level=Location.AdministrativeLevel.COUNTRY,
            country_code="ID",
        )
        papua_selatan = Location.objects.create(
            name="Papua Selatan",
            code="91",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            parent=indonesia,
            country_code="ID",
        )
        Location.objects.create(
            name="Asmat",
            code="91.18",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            parent=papua_selatan,
            country_code="ID",
        )
        kalimantan_selatan = Location.objects.create(
            name="Kalimantan Selatan",
            code="63",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            parent=indonesia,
            country_code="ID",
        )
        Location.objects.create(
            name="Kota baru",
            code="63.02",
            administrative_level=Location.AdministrativeLevel.CITY,
            parent=kalimantan_selatan,
            country_code="ID",
        )

        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            verbosity=0,
        )

        self.assertTrue(
            Location.objects.filter(
                code="93.04",
                name="Kabupaten Asmat",
                parent__code="93",
                administrative_level=Location.AdministrativeLevel.REGENCY,
            ).exists()
        )
        self.assertTrue(
            Location.objects.filter(
                code="63.02",
                name="Kabupaten Kotabaru",
                administrative_level=Location.AdministrativeLevel.REGENCY,
            ).exists()
        )

    def test_sync_merges_normalized_duplicate_and_moves_relations(self):
        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            verbosity=0,
        )
        canonical = Location.objects.get(
            code="32.01",
            name="Kabupaten Bogor",
        )
        duplicate = Location.objects.create(
            name="Bogor",
            code="32.01",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            parent=canonical.parent,
            latitude=canonical.latitude,
            longitude=canonical.longitude,
            country_code="ID",
            is_active=True,
        )
        LocationAlias.objects.create(
            location=duplicate,
            alias="Kab Bogor Lama",
        )
        source = Source.objects.create(
            name="Media Duplikat Lokasi",
            code="media-duplikat-lokasi",
            domain="location-duplicate.example.com",
            base_url="https://location-duplicate.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )
        article = Article.objects.create(
            source=source,
            original_url="https://location-duplicate.example.com/dbd-bogor",
            normalized_url="https://location-duplicate.example.com/dbd-bogor",
            title="Kasus DBD di Kabupaten Bogor",
            content_text="Sebanyak 42 kasus DBD terjadi di Kabupaten Bogor.",
            content_hash="7" * 64,
        )
        ArticleLocation.objects.create(
            article=article,
            location=duplicate,
            mention_text="Bogor",
            is_primary=True,
        )
        fact = ArticleFact.objects.create(
            article=article,
            location=duplicate,
            case_count=42,
            fact_text="Sebanyak 42 kasus DBD terjadi di Kabupaten Bogor.",
        )

        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            deactivate_unmatched=True,
            verbosity=0,
        )

        duplicate.refresh_from_db()
        fact.refresh_from_db()
        self.assertFalse(duplicate.is_active)
        self.assertEqual(
            ArticleLocation.objects.get(article=article).location,
            canonical,
        )
        self.assertEqual(fact.location, canonical)
        self.assertTrue(
            LocationAlias.objects.filter(
                location=canonical,
                alias="Kab Bogor Lama",
            ).exists()
        )
        call_command(
            "audit_indonesia_geolocation",
            strict=True,
            verbosity=0,
        )

    def test_unmatched_location_is_only_deactivated_when_requested(self):
        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            verbosity=0,
        )
        indonesia = Location.objects.get(code="ID")
        stale = Location.objects.create(
            name="Kota Data Lama",
            code="31.99",
            administrative_level=Location.AdministrativeLevel.CITY,
            parent=Location.objects.get(code="31", parent=indonesia),
            latitude=Decimal("-6.200000"),
            longitude=Decimal("106.800000"),
            country_code="ID",
            is_active=True,
        )

        call_command(
            "sync_indonesia_core_locations",
            deactivate_unmatched=True,
            verbosity=0,
        )
        stale.refresh_from_db()
        self.assertTrue(stale.is_active)

        call_command(
            "sync_indonesia_core_locations",
            apply=True,
            deactivate_unmatched=True,
            verbosity=0,
        )
        stale.refresh_from_db()
        self.assertFalse(stale.is_active)
