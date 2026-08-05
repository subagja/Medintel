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
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    ExtractionMethod,
    Location,
    LocationAlias,
    ValidationStatus,
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
        self.banten = Location.objects.create(
            name="Banten",
            code="36",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=self.indonesia,
            latitude=Decimal("-6.405817"),
            longitude=Decimal("106.064018"),
            country_code="ID",
        )
        self.dki_jakarta = Location.objects.create(
            name="DKI Jakarta",
            code="31",
            administrative_level=(
                Location.AdministrativeLevel.PROVINCE
            ),
            parent=self.indonesia,
            latitude=Decimal("-6.200000"),
            longitude=Decimal("106.816666"),
            country_code="ID",
        )
        LocationAlias.objects.create(
            location=self.dki_jakarta,
            alias="Jakarta",
        )
        self.kabupaten_tangerang = Location.objects.create(
            name="Kabupaten Tangerang",
            code="36.03",
            administrative_level=(
                Location.AdministrativeLevel.REGENCY
            ),
            parent=self.banten,
            latitude=Decimal("-6.178193"),
            longitude=Decimal("106.539423"),
            country_code="ID",
        )
        self.kota_tangerang = Location.objects.create(
            name="Kota Tangerang",
            code="36.71",
            administrative_level=(
                Location.AdministrativeLevel.CITY
            ),
            parent=self.banten,
            latitude=Decimal("-6.175491"),
            longitude=Decimal("106.667319"),
            country_code="ID",
        )
        LocationAlias.objects.create(
            location=self.kabupaten_tangerang,
            alias="Tangerang",
        )
        LocationAlias.objects.create(
            location=self.kabupaten_tangerang,
            alias="Kab. Tangerang",
        )
        LocationAlias.objects.create(
            location=self.kota_tangerang,
            alias="Tangerang",
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

    def test_reconcile_does_not_change_foreign_location(self):
        article = Article.objects.create(
            source=self.source,
            original_url="https://geo.example.com/michigan",
            normalized_url="https://geo.example.com/michigan",
            title="Dua kematian akibat siklosporiasis di Michigan",
            content_text=(
                "Otoritas Amerika Serikat melaporkan kejadian "
                "tersebut di Michigan."
            ),
            content_hash="e" * 64,
        )
        relation = ArticleLocation.objects.create(
            article=article,
            location=self.michigan,
            mention_text="Michigan",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
            is_primary=True,
        )
        fact = ArticleFact.objects.create(
            article=article,
            location=self.michigan,
            death_count=2,
            fact_text="Dua kematian dilaporkan di Michigan.",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
        )

        call_command(
            "reconcile_article_geolocation",
            article_id=article.id,
            apply=True,
            verbosity=0,
        )

        relation.refresh_from_db()
        fact.refresh_from_db()
        self.assertEqual(relation.location, self.michigan)
        self.assertTrue(relation.is_primary)
        self.assertEqual(fact.location, self.michigan)

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

    def test_title_location_excludes_secondary_body_location(self):
        article = Article.objects.create(
            source=self.source,
            original_url="https://geo.example.com/tangerang-secondary",
            normalized_url="https://geo.example.com/tangerang-secondary",
            title="5.101 kasus TB di Kabupaten Tangerang",
            content_text=(
                "Dinas Kesehatan Kabupaten Tangerang mencatat "
                "5.101 kasus tuberkulosis. Pelayanan rujukan juga "
                "tersedia di Kota Tangerang."
            ),
            content_hash="7" * 64,
        )

        result = resolve_indonesia_locations(
            f"{article.title}\n{article.content_text}",
            title_length=len(article.title),
        )
        self.assertEqual(
            result.primary.location_id,
            str(self.kabupaten_tangerang.id),
        )
        self.assertEqual(len(result.mentions), 2)

        extract_article_entities(article)
        relation = ArticleLocation.objects.get(article=article)
        self.assertEqual(
            relation.location,
            self.kabupaten_tangerang,
        )
        self.assertTrue(relation.is_primary)

    def test_multiregion_without_title_requires_review(self):
        article = Article.objects.create(
            source=self.source,
            original_url="https://geo.example.com/multiregion",
            normalized_url="https://geo.example.com/multiregion",
            title="Penguatan surveilans penyakit",
            content_text=(
                "Sebanyak 42 kasus DBD dilaporkan di Kota Bandung. "
                "Kabupaten Tangerang juga meningkatkan surveilans."
            ),
            content_hash="8" * 64,
        )
        legacy = ArticleLocation.objects.create(
            article=article,
            location=self.kota_bandung,
            mention_text="Kota Bandung",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
            is_primary=True,
        )

        result = resolve_indonesia_locations(
            f"{article.title}\n{article.content_text}",
            title_length=len(article.title),
        )
        self.assertIsNone(result.primary)

        call_command(
            "reconcile_article_geolocation",
            article_id=article.id,
            apply=True,
            verbosity=0,
        )
        legacy.refresh_from_db()
        self.assertTrue(legacy.is_primary)

    def test_dateline_is_not_event_location(self):
        title = "Kerentanan hepatitis B masih tinggi"
        result = resolve_indonesia_locations(
            (
                f"{title}\n"
                "Jakarta (ANTARA) - Kementerian Kesehatan "
                "menyatakan 60 persen penduduk masih rentan."
            ),
            title_length=len(title),
        )

        self.assertEqual(len(result.mentions), 1)
        self.assertIsNone(result.primary)

    def test_single_child_parent_cluster_selects_child(self):
        title = "Pengendalian DBD terus diperkuat"
        result = resolve_indonesia_locations(
            (
                f"{title}\n"
                "Sebanyak 42 kasus DBD terjadi di Kota Bandung, "
                "Jawa Barat."
            ),
            title_length=len(title),
        )

        self.assertEqual(
            result.primary.location_id,
            str(self.kota_bandung.id),
        )

    def _create_tangerang_article(self, suffix: str) -> Article:
        return Article.objects.create(
            source=self.source,
            original_url=(
                f"https://geo.example.com/tb-tangerang-{suffix}"
            ),
            normalized_url=(
                f"https://geo.example.com/tb-tangerang-{suffix}"
            ),
            title="5.101 kasus TB di Kabupaten Tangerang",
            content_text=(
                "Dinas Kesehatan Kabupaten Tangerang mencatat "
                "5.101 kasus tuberkulosis."
            ),
            content_hash=(suffix[0] * 64),
        )

    def test_explicit_regency_does_not_create_same_named_city(self):
        article = self._create_tangerang_article("a")

        extract_article_entities(article)

        relations = ArticleLocation.objects.filter(
            article=article,
        )
        self.assertEqual(relations.count(), 1)
        self.assertEqual(
            relations.get().location,
            self.kabupaten_tangerang,
        )
        self.assertTrue(relations.get().is_primary)

    def test_reextract_removes_unreviewed_legacy_false_positive(self):
        article = self._create_tangerang_article("b")
        ArticleLocation.objects.create(
            article=article,
            location=self.kota_tangerang,
            mention_text="Tangerang",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
            is_primary=True,
        )

        extract_article_entities(article)

        self.assertFalse(
            ArticleLocation.objects.filter(
                article=article,
                location=self.kota_tangerang,
            ).exists()
        )
        relation = ArticleLocation.objects.get(
            article=article,
            location=self.kabupaten_tangerang,
        )
        self.assertTrue(relation.is_primary)

    def test_reextract_preserves_analyst_corrected_location(self):
        article = self._create_tangerang_article("c")
        protected = ArticleLocation.objects.create(
            article=article,
            location=self.kota_tangerang,
            mention_text="Tangerang",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.CORRECTED,
            is_primary=True,
        )

        extract_article_entities(article)

        protected.refresh_from_db()
        self.assertTrue(protected.is_primary)
        self.assertEqual(
            protected.validation_status,
            ValidationStatus.CORRECTED,
        )
        self.assertFalse(
            ArticleLocation.objects.filter(
                article=article,
                location=self.kabupaten_tangerang,
            ).exists()
        )

    def test_reconcile_command_is_dry_run_then_applies(self):
        article = self._create_tangerang_article("d")
        ArticleLocation.objects.create(
            article=article,
            location=self.kota_tangerang,
            mention_text="Tangerang",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
            is_primary=True,
        )
        automatic_fact = ArticleFact.objects.create(
            article=article,
            location=self.kota_tangerang,
            case_count=5101,
            fact_text="5.101 kasus TB di Kabupaten Tangerang.",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.UNREVIEWED,
        )
        protected_fact = ArticleFact.objects.create(
            article=article,
            location=self.kota_tangerang,
            death_count=1,
            fact_text="Fakta yang sudah divalidasi analis.",
            extraction_method=ExtractionMethod.SYSTEM,
            validation_status=ValidationStatus.VALIDATED,
        )
        validated_disease = ArticleDisease.objects.create(
            article=article,
            disease=self.disease,
            mention_text="TB",
            extraction_method=ExtractionMethod.RULE_BASED,
            validation_status=ValidationStatus.VALIDATED,
            is_primary=True,
        )

        call_command(
            "reconcile_article_geolocation",
            article_id=article.id,
            verbosity=0,
        )
        self.assertTrue(
            ArticleLocation.objects.filter(
                article=article,
                location=self.kota_tangerang,
            ).exists()
        )
        self.assertFalse(
            ArticleLocation.objects.filter(
                article=article,
                location=self.kabupaten_tangerang,
            ).exists()
        )
        automatic_fact.refresh_from_db()
        protected_fact.refresh_from_db()
        validated_disease.refresh_from_db()
        self.assertEqual(
            automatic_fact.location,
            self.kota_tangerang,
        )
        self.assertEqual(
            protected_fact.location,
            self.kota_tangerang,
        )
        self.assertEqual(
            validated_disease.validation_status,
            ValidationStatus.VALIDATED,
        )

        call_command(
            "reconcile_article_geolocation",
            article_id=article.id,
            apply=True,
            verbosity=0,
        )
        self.assertFalse(
            ArticleLocation.objects.filter(
                article=article,
                location=self.kota_tangerang,
            ).exists()
        )
        self.assertTrue(
            ArticleLocation.objects.get(
                article=article,
                location=self.kabupaten_tangerang,
            ).is_primary
        )
        automatic_fact.refresh_from_db()
        protected_fact.refresh_from_db()
        self.assertEqual(
            automatic_fact.location,
            self.kabupaten_tangerang,
        )
        self.assertEqual(
            protected_fact.location,
            self.kota_tangerang,
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
