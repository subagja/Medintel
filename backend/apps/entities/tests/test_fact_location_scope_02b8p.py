"""02B8P: koreksi cakupan lokasi fakta dan tanggal dateline, wajib tercatat."""
from datetime import date
from django.test import TestCase, override_settings
from apps.locations.models import Location
from apps.entities.models import ExtractionReviewLog


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class FactLocationCorrectionTests(TestCase):
    def setUp(self):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        ReportingPeriodReviewTests.setUp(self)
        self.province = Location.objects.create(
            name="Provinsi Uji 02B8P", administrative_level="province", country_code="ID"
        )
        self.location.parent = self.province
        self.location.save(update_fields=["parent"])

    def url(self):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        return ReportingPeriodReviewTests.url(self)

    def payload(self, **kwargs):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        return ReportingPeriodReviewTests.payload(self, **kwargs)

    def test_parent_location_can_be_selected_from_existing_fact(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Koreksi lokasi FAKTA')
        self.assertContains(response, str(self.province.pk))
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.location_id, self.location.pk)

    def test_manual_location_change_keeps_other_facts_and_writes_audit(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(
            self.url(), self.payload(corrected_location=str(self.province.pk))
        )
        self.assertEqual(response.status_code, 302)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.location_id, self.province.pk)
        log = ExtractionReviewLog.objects.get(
            object_type=ExtractionReviewLog.ObjectType.ARTICLE_FACT,
            object_id=self.fact.pk,
            action=ExtractionReviewLog.Action.CORRECT,
        )
        self.assertEqual(log.before_data["location_id"], str(self.location.pk))
        self.assertEqual(log.after_data["location_id"], str(self.province.pk))
        self.assertTrue(log.notes)

    def test_date_from_datelin_must_be_explicitly_cleared(self):
        self.fact.event_date = date(2026, 5, 21)
        self.fact.save(update_fields=["event_date", "updated_at"])
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 200)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.event_date, date(2026, 5, 21))
        response = self.client.post(
            self.url(), self.payload(
                corrected_location=str(self.province.pk),
                clear_single_event_date="on",
                correction_reason="Tanggal terbit bukan tanggal onset, sesuai sumber artikel.",
            )
        )
        self.assertEqual(response.status_code, 302)
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.event_date)
        self.assertEqual(self.fact.location_id, self.province.pk)
        self.assertEqual(self.fact.reporting_period_start, date(2026, 1, 1))

    def test_unknown_location_is_rejected(self):
        other = Location.objects.create(
            name="Wilayah Asing 02B8P", administrative_level="province", country_code="ID"
        )
        self.client.force_login(self.reviewer)
        response = self.client.post(
            self.url(), self.payload(corrected_location=str(other.pk))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Belum tersimpan")
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.location_id, self.location.pk)
        self.assertFalse(ExtractionReviewLog.objects.filter(object_id=self.fact.pk).exists())

    def test_legacy_submission_does_not_clear_existing_location(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url(), self.payload())
        self.assertEqual(response.status_code, 302)
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.location_id, self.location.pk)
