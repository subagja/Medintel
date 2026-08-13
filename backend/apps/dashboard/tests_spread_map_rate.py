from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.articles.models import Article
from apps.entities.models import ArticleFact, Disease
from apps.locations.models import Location
from apps.sources.models import Source

from .spread_map_metrics import (
    ROLLING_WINDOW_DAYS,
    province_population_reference,
)


User = get_user_model()


class SpreadMapRateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="spread-map-rate-admin",
            password="test-password-123",
            email="spread-map@example.com",
        )
        self.client.force_login(self.user)
        self.source = Source.objects.create(
            name="Media Uji Peta Sebaran",
            code="media-uji-spread-map",
            domain="spread-map.example.com",
            base_url="https://spread-map.example.com",
            source_type=Source.SourceType.LOCAL_MEDIA,
            is_verified=True,
        )
        self.disease = Disease.objects.create(
            name="Penyakit Uji Peta Sebaran",
            code="penyakit-uji-peta-sebaran",
        )
        self.province = Location.objects.create(
            name="Banten",
            code="36",
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            country_code="ID",
        )
        self.regency = Location.objects.create(
            name="Kabupaten Tangerang",
            code="3603",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            parent=self.province,
            country_code="ID",
        )
        self.event_date = date(2026, 8, 13)
        self.fact = self._fact(
            suffix="utama",
            event_date=self.event_date,
            case_count=100,
        )
        self.url = reverse("dashboard:spread-map-data")

    def _fact(self, *, suffix, event_date, case_count):
        article = Article.objects.create(
            source=self.source,
            original_url=f"https://spread-map.example.com/{suffix}",
            normalized_url=f"https://spread-map.example.com/{suffix}",
            title=f"Laporan {case_count} kasus {suffix}",
            content_text=f"Dilaporkan {case_count} kasus di Tangerang.",
            content_hash=(suffix * 64)[:64],
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        return ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.regency,
            event_date=event_date,
            case_count=case_count,
            fact_text=f"Dilaporkan {case_count} kasus.",
        )

    def _latest_entry(self, *, level, code):
        response = self.client.get(
            self.url,
            {"disease": self.disease.code, "level": level},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        return payload, payload["timeline"][-1]["locations"][code]

    def test_population_reference_contains_all_38_provinces(self):
        reference = province_population_reference()

        self.assertEqual(len(reference), 38)
        self.assertEqual(reference["36"]["population"], 12_537_400)
        self.assertEqual(reference["32"]["population"], 50_759_000)

    def test_province_level_returns_rate_per_100k(self):
        payload, entry = self._latest_entry(level="province", code="36")

        self.assertTrue(payload["metric"]["normalized"])
        self.assertEqual(entry["reported_case_count"], 100)
        self.assertEqual(entry["population"], 12_537_400)
        self.assertAlmostEqual(entry["metric_value"], 0.7976, places=4)

    def test_regency_level_keeps_raw_reported_case_count(self):
        payload, entry = self._latest_entry(
            level="regency_city",
            code="3603",
        )

        self.assertFalse(payload["metric"]["normalized"])
        self.assertEqual(entry["reported_case_count"], 100)
        self.assertEqual(entry["metric_value"], 100.0)
        self.assertIsNone(entry["population"])

    def test_duplicate_value_from_another_article_is_counted_once(self):
        second_source = Source.objects.create(
            name="Media Uji Peta Sebaran Kedua",
            code="media-uji-spread-map-kedua",
            domain="spread-map-2.example.com",
            base_url="https://spread-map-2.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
        )
        article = Article.objects.create(
            source=second_source,
            original_url="https://spread-map-2.example.com/duplikat",
            normalized_url="https://spread-map-2.example.com/duplikat",
            title="Media kedua melaporkan angka yang sama",
            content_text="Dilaporkan 100 kasus di Kabupaten Tangerang.",
            content_hash="d" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        ArticleFact.objects.create(
            article=article,
            disease=self.disease,
            location=self.regency,
            event_date=self.event_date,
            case_count=100,
            fact_text="Dilaporkan 100 kasus.",
        )

        _payload, entry = self._latest_entry(
            level="regency_city",
            code="3603",
        )

        self.assertEqual(entry["reported_case_count"], 100)
        self.assertEqual(entry["article_count"], 2)
        self.assertEqual(entry["source_count"], 2)

    def test_latest_frame_uses_rolling_14_day_window(self):
        older_date = self.event_date - timedelta(days=ROLLING_WINDOW_DAYS + 2)
        self.fact.event_date = older_date
        self.fact.save(update_fields=["event_date", "updated_at"])
        self._fact(
            suffix="terbaru",
            event_date=self.event_date,
            case_count=25,
        )

        payload, entry = self._latest_entry(
            level="regency_city",
            code="3603",
        )

        latest_frame = payload["timeline"][-1]
        self.assertEqual(entry["reported_case_count"], 25)
        self.assertEqual(
            latest_frame["period_start"],
            (self.event_date - timedelta(days=13)).isoformat(),
        )

    def test_page_explains_metric_limitations(self):
        response = self.client.get(reverse("dashboard:spread-map"))

        self.assertContains(response, "bukan angka epidemiologis")
        self.assertContains(
            response,
            "Rasio Kasus Terlapor OSINT per 100.000 Penduduk",
        )
        self.assertContains(
            response,
            "Belum dinormalisasi berdasarkan jumlah penduduk.",
        )
