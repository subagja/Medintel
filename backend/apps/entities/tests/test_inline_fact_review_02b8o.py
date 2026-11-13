"""02B8O: pengujian presentasi modal tanpa mengubah mekanisme koreksi fakta."""
from django.test import TestCase, override_settings


# Reuse fixture factory only, without inheriting its tests.
@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class InlineFactReviewTests(TestCase):
    def setUp(self):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        ReportingPeriodReviewTests.setUp(self)

    def url(self):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        return ReportingPeriodReviewTests.url(self)

    def payload(self, **kwargs):
        from apps.entities.tests.test_reporting_period_02b8n import ReportingPeriodReviewTests
        return ReportingPeriodReviewTests.payload(self, **kwargs)

    def test_inline_get_is_compact_and_no_sidebar(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.url() + "?embed=1")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="medintel-inline-fact-workspace"')
        self.assertNotContains(response, 'id="medintel-sidebar"')
        self.assertContains(response, 'action="?embed=1"')
        # Endpoint koreksi tetap tidak dapat dibingkai oleh situs luar.
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_direct_link_still_works(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="fact-period-correction-form"')
        self.assertNotContains(response, 'id="medintel-inline-fact-workspace"')

    def test_successful_embedded_save_keeps_modal_context_and_data(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(self.url() + "?embed=1", self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertIn("embed=1&saved=1", response["Location"])
        response = self.client.get(response["Location"])
        self.assertContains(response, "Koreksi berhasil disimpan")
        self.assertContains(response, "medintel:fact-review-saved")
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.death_count, 1)
        self.assertIsNone(self.fact.event_date)

    def test_invalid_embed_post_shows_error_and_preserves_form_draft(self):
        self.client.force_login(self.reviewer)
        response = self.client.post(
            self.url() + "?embed=1", self.payload(reporting_period_end="2025-07-31")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Belum tersimpan")
        self.assertContains(response, 'value="2025-07-31"')
        self.assertContains(response, 'id="medintel-inline-fact-workspace"')
        self.fact.refresh_from_db()
        self.assertIsNone(self.fact.reporting_period_start)

    def test_article_workspace_has_in_place_review_link(self):
        self.client.force_login(self.reviewer)
        response = self.client.get("/validasi-artikel/", {
            "workspace": "history", "history_status": "validated",
            "article": str(self.article.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="medintel-fact-review-modal"')
        self.assertContains(response, 'data-medintel-inline-fact-review')
