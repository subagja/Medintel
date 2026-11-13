"""Regresi faktual 02B8K: bukti numerik harus berasal dari isi bila tersedia."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from apps.entities.services.fact_extraction import (
    build_fact_candidate,
    preferred_body_text,
)


class PreferredEvidenceTests(SimpleTestCase):
    def article(self, title, content):
        return SimpleNamespace(
            title=title,
            content_text=content,
            published_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )

    def candidate(self, article):
        with patch(
            "apps.entities.services.fact_extraction.get_primary_disease",
            return_value=None,
        ), patch(
            "apps.entities.services.fact_extraction.get_primary_location",
            return_value=None,
        ):
            return build_fact_candidate(article)

    def test_prefers_body_evidence_over_headline(self):
        article = self.article(
            "534 Kasus DBD di Pekanbaru hingga Juli",
            "Berdasarkan data terbaru, tercatat sebanyak 534 kasus DBD "
            "di Kota Pekanbaru dalam periode Januari hingga Juli 2026.",
        )
        result = self.candidate(article)
        self.assertEqual(result.case_count, 534)
        self.assertIn("534 kasus DBD di Kota Pekanbaru", result.evidence_text)
        self.assertNotEqual(result.evidence_text.strip(), article.title)

    def test_numeric_quote_wins_over_earlier_trend_sentence(self):
        article = self.article(
            "Kasus DBD di Badung naik",
            "Kasus DBD di Kabupaten Badung mengalami peningkatan. "
            "Periode Januari hingga April 2024, tercatat ada 859 kasus.",
        )
        result = self.candidate(article)
        self.assertEqual(result.case_count, 859)
        self.assertIn("859 kasus", result.evidence_text)

    def test_deduplicates_headline_in_start_of_body_only(self):
        title = "Kasus Rabies Jembrana Turun, dari 116 Jadi 88"
        content = (
            "Bali > Jembrana > " + title + " "
            "NEGARA – Jumlah kasus rabies positif di Kabupaten Jembrana "
            "tahun 2026 tercatat 88 kasus, turun dari 116 kasus tahun 2025."
        )
        processed = preferred_body_text(title, content)
        self.assertNotIn("> " + title, processed)
        result = self.candidate(self.article(title, content))
        self.assertEqual(result.case_count, 88)
        self.assertIn("Jumlah kasus rabies positif", result.evidence_text)

    def test_content_count_stays_separate_from_location(self):
        # Informasi 263 untuk Provinsi Aceh; algoritma ini hanya memilih
        # kutipan, BUKAN menetapkan hubungan lokasi utama otomatis.
        article = self.article(
            "Kemenkes Jadikan Aceh Percontohan Imunisasi",
            "Hingga minggu ke-19 tahun 2026, tercatat ada 263 kasus "
            "campak di Provinsi Aceh, dengan 24 kasus di antaranya "
            "berada di Kota Banda Aceh.",
        )
        result = self.candidate(article)
        self.assertEqual(result.case_count, 263)
        self.assertIn("263 kasus campak di Provinsi Aceh", result.evidence_text)

    def test_headline_fallback_when_body_has_no_numeric_claim(self):
        article = self.article(
            "42 kasus DBD di wilayah uji",
            "Dinas kesehatan meningkatkan kewaspadaan dan koordinasi.",
        )
        result = self.candidate(article)
        self.assertEqual(result.case_count, 42)
        self.assertIn("42 kasus DBD", result.evidence_text)

    def test_title_not_removed_from_middle_of_article(self):
        title = "42 kasus DBD di wilayah uji"
        content = "A"*370 + " " + title + " tetapi belum diperbarui."
        self.assertEqual(preferred_body_text(title, content), content)
