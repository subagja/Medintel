"""Regression: headline substring inside a case sentence is not duplicate."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from apps.entities.services.fact_extraction import build_fact_candidate, preferred_body_text


class InlineHeadlineGuardTests(SimpleTestCase):
    def test_dont_strip_title_words_inside_numeric_sentence(self):
        title = "Kasus DBD di Kabupaten Uji Rekonsiliasi"
        content = "Sebanyak 88 kasus DBD di Kabupaten Uji Rekonsiliasi."
        self.assertEqual(preferred_body_text(title, content), content)
        article = SimpleNamespace(title=title, content_text=content,
            published_at=datetime(2026, 11, 11, tzinfo=timezone.utc))
        with patch("apps.entities.services.fact_extraction.get_primary_disease", return_value=None), \
             patch("apps.entities.services.fact_extraction.get_primary_location", return_value=None):
            candidate = build_fact_candidate(article)
        self.assertEqual(candidate.case_count, 88)
        self.assertIn("88 kasus DBD", candidate.evidence_text)

    def test_strip_real_heading_at_start(self):
        title = "Kasus DBD di Kabupaten Uji Rekonsiliasi"
        body = title + "\nSebanyak 88 kasus DBD di Kabupaten Uji Rekonsiliasi."
        processed = preferred_body_text(title, body)
        self.assertTrue(processed.startswith("Sebanyak 88 kasus"))

    def test_strip_breadcrumb_heading(self):
        title = "Kasus Rabies Jembrana Turun, dari 116 Jadi 88"
        body = ("Bali > Jembrana > " + title + " NEGARA – "
                "Jumlah kasus rabies di Jembrana tercatat 88 kasus.")
        processed = preferred_body_text(title, body)
        self.assertNotIn(title, processed)
        self.assertIn("88 kasus", processed)

    def test_preserve_headline_quoted_inside_sentence(self):
        title = "42 kasus DBD di wilayah uji"
        body = "Petugas menegaskan bahwa 42 kasus DBD di wilayah uji masih ditangani."
        self.assertEqual(preferred_body_text(title, body), body)
