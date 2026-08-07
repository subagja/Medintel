from datetime import date
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.articles.models import Article
from apps.assessments.models import EarlyWarning, SignalAssessment
from apps.assessments.services.threat_map import (
    build_threat_map_dataset,
    resolve_province,
)
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal, SignalArticle
from apps.sources.models import Source


User = get_user_model()


class ThreatMapWorkspaceTests(TestCase):
    def setUp(self):
        self.analyst = User.objects.create_user(
            username="threat-map-analyst",
            password="test-password-123",
        )
        self.client.force_login(self.analyst)

        self.province = self._province(
            name="Banten",
            code="36",
            latitude="-6.505923",
            longitude="105.937626",
        )
        self.location, _ = Location.objects.get_or_create(
            name="Kabupaten Tangerang",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            parent=self.province,
            country_code="ID",
            defaults={
                "code": "3603",
                "latitude": "-6.176100",
                "longitude": "106.638900",
            },
        )
        self.disease, _ = Disease.objects.get_or_create(
            name="Tuberkulosis",
            defaults={"code": "tb-threat-map"},
        )
        self.source = Source.objects.create(
            name="RRI Threat Map",
            code="rri-threat-map",
            domain="rri-map.example.com",
            base_url="https://rri-map.example.com",
            source_type=Source.SourceType.GOVERNMENT,
            is_verified=True,
            is_active=True,
        )
        self.article = Article.objects.create(
            source=self.source,
            original_url="https://rri-map.example.com/tb-tangerang",
            normalized_url="https://rri-map.example.com/tb-tangerang",
            title="Dinkes catat 5.101 kasus Tuberkulosis",
            content_text=(
                "Dinas Kesehatan mencatat 5.101 kasus Tuberkulosis "
                "di Kabupaten Tangerang."
            ),
            content_hash="m" * 64,
            processing_status=Article.ProcessingStatus.PROCESSED,
        )
        self.signal = self._signal(
            code="SIG-MAP-0001",
            disease=self.disease,
            location=self.location,
        )
        SignalArticle.objects.create(
            signal=self.signal,
            article=self.article,
            support_type=SignalArticle.SupportType.PRIMARY,
            is_primary_source=True,
        )
        self.assessment = self._assessment(self.signal, version=1)
        self.warning = self._warning(
            signal=self.signal,
            assessment=self.assessment,
            code="PD-MAP-0001",
            level=EarlyWarning.Level.ADVISORY,
        )
        self.url = reverse("dashboard:threat-map")

    @staticmethod
    def _province(*, name, code, latitude, longitude):
        existing = Location.objects.filter(
            name=name,
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            country_code="ID",
        ).first()
        if existing:
            return existing
        return Location.objects.create(
            name=name,
            code=code,
            administrative_level=Location.AdministrativeLevel.PROVINCE,
            country_code="ID",
            latitude=latitude,
            longitude=longitude,
        )

    def _signal(self, *, code, disease, location):
        return Signal.objects.create(
            code=code,
            title=f"Pelaporan kasus {disease.name}",
            summary=f"Pelaporan kasus {disease.name} di {location.name}.",
            primary_disease=disease,
            primary_location=location,
            event_start_date=date(2026, 8, 5),
            status=Signal.Status.VALIDATED,
            validated_by=self.analyst,
            analyst_judgement="Sinyal relevan untuk pemantauan.",
        )

    def _assessment(self, signal, *, version):
        return SignalAssessment.objects.create(
            signal=signal,
            version=version,
            is_current=True,
            status=SignalAssessment.Status.COMPLETED,
            urgency_score=3,
            impact_score=3,
            geographic_scope_score=3,
            development_speed_score=3,
            vulnerability_score=3,
            source_reliability_score=0.80,
            information_credibility_score=0.80,
            information_completeness_score=0.60,
            evidence_consistency_score=0.80,
            priority_score=0.60,
            confidence_score=0.72,
            recommended_priority=(
                SignalAssessment.RecommendedPriority.MEDIUM
            ),
            recommended_confidence=(
                SignalAssessment.RecommendedConfidence.MEDIUM
            ),
            analytical_judgement="Perlu pemantauan dan verifikasi.",
            implications="Berpotensi menambah beban layanan.",
            recommended_actions="Verifikasi kepada Dinas Kesehatan.",
            limitations="Belum ada pembanding periode sebelumnya.",
            assessed_by=self.analyst,
            completed_at=timezone.now(),
        )

    def _warning(self, *, signal, assessment, code, level):
        return EarlyWarning.objects.create(
            code=code,
            signal=signal,
            assessment=assessment,
            version=assessment.version,
            is_current=True,
            level=level,
            confidence_level=assessment.recommended_confidence,
            status=EarlyWarning.Status.ISSUED,
            title=f"Peringatan Dini {signal.primary_disease.name}",
            summary=signal.summary,
            analytical_judgement=assessment.analytical_judgement,
            implications=assessment.implications,
            recommended_actions=assessment.recommended_actions,
            information_gaps=assessment.limitations,
            decision_notes="Diterbitkan setelah konfirmasi analis.",
            issued_by=self.analyst,
        )

    def _second_warning(self):
        province = self._province(
            name="Jawa Barat",
            code="32",
            latitude="-6.831098",
            longitude="107.603493",
        )
        location, _ = Location.objects.get_or_create(
            name="Kota Bandung Threat Map",
            administrative_level=Location.AdministrativeLevel.CITY,
            parent=province,
            country_code="ID",
            defaults={
                "code": "3273",
                "latitude": "-6.917500",
                "longitude": "107.619100",
            },
        )
        disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={"code": "dbd-threat-map"},
        )
        signal = self._signal(
            code="SIG-MAP-0002",
            disease=disease,
            location=location,
        )
        assessment = self._assessment(signal, version=1)
        return self._warning(
            signal=signal,
            assessment=assessment,
            code="PD-MAP-0002",
            level=EarlyWarning.Level.HIGH,
        )

    def test_sidebar_opens_threat_map_workspace(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peta Ancaman")
        self.assertContains(response, f'href="{self.url}"', html=False)
        self.assertContains(response, self.warning.code)

    def test_active_warning_is_aggregated_to_parent_province(self):
        response = self.client.get(self.url)

        self.assertEqual(response.context["summary"]["active"], 1)
        self.assertEqual(response.context["summary"]["provinces"], 1)
        province = response.context["provinces"][0]
        self.assertEqual(province["name"], "Banten")
        self.assertEqual(province["level"], EarlyWarning.Level.ADVISORY)
        self.assertEqual(province["warning_count"], 1)
        self.assertEqual(
            province["warnings"][0]["location"],
            "Kabupaten Tangerang",
        )
        self.assertEqual(province["warnings"][0]["article_count"], 1)

    def test_only_current_issued_warnings_are_mapped(self):
        self.warning.status = EarlyWarning.Status.CLOSED
        self.warning.save(update_fields=["status", "updated_at"])

        response = self.client.get(self.url)

        self.assertEqual(response.context["summary"]["active"], 0)
        self.assertEqual(response.context["provinces"], [])
        self.assertNotContains(response, self.warning.code)

    def test_disease_and_level_filters_limit_map_data(self):
        second_warning = self._second_warning()

        response = self.client.get(
            self.url,
            {
                "disease": str(second_warning.signal.primary_disease_id),
                "level": EarlyWarning.Level.HIGH,
            },
        )

        self.assertEqual(response.context["summary"]["active"], 1)
        self.assertEqual(
            response.context["warnings"][0]["code"],
            second_warning.code,
        )
        self.assertNotContains(response, self.warning.code)

    def test_highest_warning_sets_province_colour_level(self):
        second_assessment = self.assessment
        self.warning.is_current = False
        self.warning.status = EarlyWarning.Status.SUPERSEDED
        self.warning.save(
            update_fields=["is_current", "status", "updated_at"]
        )
        second_assessment.is_current = False
        second_assessment.status = SignalAssessment.Status.SUPERSEDED
        second_assessment.save(
            update_fields=["is_current", "status", "updated_at"]
        )
        new_assessment = self._assessment(self.signal, version=2)
        high_warning = self._warning(
            signal=self.signal,
            assessment=new_assessment,
            code="PD-MAP-0003",
            level=EarlyWarning.Level.CRITICAL,
        )

        dataset = build_threat_map_dataset([high_warning])

        self.assertEqual(dataset.provinces[0]["level"], "critical")
        self.assertEqual(dataset.provinces[0]["severity"], 4)

    def test_resolves_province_from_bps_code_when_parent_is_missing(self):
        orphan = Location.objects.create(
            name="Kabupaten Tangerang Tanpa Parent",
            code="3603",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )

        self.assertEqual(resolve_province(orphan), self.province)

    def test_page_states_that_map_does_not_declare_outbreak(self):
        response = self.client.get(self.url)

        self.assertContains(response, "tidak menyatakan KLB")
        self.assertContains(response, "indonesia_provinces.geojson")

    def test_geojson_exterior_rings_use_d3_clockwise_orientation(self):
        geojson_path = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "assessments"
            / "data"
            / "indonesia_provinces.geojson"
        )
        geojson = json.loads(geojson_path.read_text(encoding="utf-8"))

        self.assertEqual(len(geojson["features"]), 38)
        for feature in geojson["features"]:
            geometry = feature["geometry"]
            polygons = (
                [geometry["coordinates"]]
                if geometry["type"] == "Polygon"
                else geometry["coordinates"]
            )
            for polygon in polygons:
                self.assertLess(
                    self._signed_ring_area(polygon[0]),
                    0,
                    msg=(
                        f"Cincin luar {feature['properties']['name']} "
                        "harus searah jarum jam untuk proyeksi D3."
                    ),
                )
                for hole in polygon[1:]:
                    self.assertGreater(
                        self._signed_ring_area(hole),
                        0,
                        msg=(
                            f"Cincin dalam {feature['properties']['name']} "
                            "harus berlawanan arah jarum jam."
                        ),
                    )

    @staticmethod
    def _signed_ring_area(ring):
        return sum(
            (x1 * y2) - (x2 * y1)
            for (x1, y1), (x2, y2) in zip(ring, ring[1:])
        ) / 2

    def test_geojson_contains_all_indonesia_provinces(self):
        geojson_path = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "assessments"
            / "data"
            / "indonesia_provinces.geojson"
        )

        self.assertTrue(geojson_path.exists())
        self.assertIn('"features"', geojson_path.read_text(encoding="utf-8"))

    def test_map_emphasizes_and_focuses_selected_warning_province(self):
        response = self.client.get(self.url)

        self.assertContains(
            response,
            "--threat-none: var(--tblr-gray-200)",
            html=False,
        )
        self.assertContains(response, '" has-warning"', html=False)
        self.assertContains(response, "focusOnProvince", html=False)
        self.assertContains(response, "threat-marker-halo", html=False)
        self.assertContains(response, "Math.min(3.6", html=False)

    def test_early_warning_workspace_links_to_map(self):
        response = self.client.get(
            reverse("dashboard:early-warning"),
            {"assessment": str(self.assessment.pk)},
        )

        expected = f"{self.url}?warning={self.warning.pk}"
        self.assertContains(response, expected)
