from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.articles.models import Article
from apps.entities.models import Disease
from apps.locations.models import Location
from apps.signals.models import Signal, SignalArticle
from apps.signals.services.correlation import (
    attach_article_to_signal,
    detach_article_from_signal,
    merge_signals,
    split_signal,
    update_article_support_type,
)
from apps.sources.models import Source

User = get_user_model()


class SignalCorrelationFixture(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(
            username="correlation-analyst",
            password="test-password-123",
        )

        self.source = Source.objects.create(
            name="Media Korelasi",
            code="media-korelasi",
            domain="korelasi.example.com",
            base_url="https://korelasi.example.com",
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
        )

        self.disease, _ = Disease.objects.get_or_create(
            name="Demam Berdarah Dengue",
            defaults={"code": "dbd-korelasi"},
        )

        self.location = Location.objects.create(
            name="Kabupaten Korelasi",
            administrative_level=Location.AdministrativeLevel.REGENCY,
            country_code="ID",
        )

    def _make_article(self, slug: str, title: str = "Artikel Uji"):
        return Article.objects.create(
            source=self.source,
            original_url=f"https://korelasi.example.com/read/{slug}",
            normalized_url=f"https://korelasi.example.com/read/{slug}",
            title=title,
            content_text="Konten uji.",
            content_hash=slug.ljust(64, "0"),
            processing_status=Article.ProcessingStatus.PROCESSED,
        )

    def _make_signal(self, code: str, status=Signal.Status.NEEDS_REVIEW):
        return Signal.objects.create(
            code=code,
            title=f"Sinyal {code}",
            summary="Ringkasan uji.",
            primary_disease=self.disease,
            primary_location=self.location,
            event_start_date=date(2026, 7, 28),
            event_end_date=date(2026, 7, 28),
            status=status,
        )


class AttachDetachArticleTests(SignalCorrelationFixture):
    def test_attach_article_creates_link_and_history(self):
        signal = self._make_signal("SIG-COR-0001")
        article = self._make_article("attach-1")

        link = attach_article_to_signal(
            signal=signal,
            article=article,
            actor=self.actor,
            support_type=SignalArticle.SupportType.CORROBORATING,
        )

        self.assertEqual(link.support_type, SignalArticle.SupportType.CORROBORATING)
        self.assertTrue(
            signal.histories.filter(
                reason__icontains="ditambahkan manual"
            ).exists()
        )

    def test_attach_duplicate_article_rejected(self):
        signal = self._make_signal("SIG-COR-0002")
        article = self._make_article("attach-2")
        attach_article_to_signal(
            signal=signal, article=article, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            attach_article_to_signal(
                signal=signal, article=article, actor=self.actor
            )

    def test_attach_to_closed_signal_rejected(self):
        signal = self._make_signal(
            "SIG-COR-0003", status=Signal.Status.CLOSED
        )
        article = self._make_article("attach-3")

        with self.assertRaises(ValidationError):
            attach_article_to_signal(
                signal=signal, article=article, actor=self.actor
            )

    def test_detach_last_article_rejected(self):
        signal = self._make_signal("SIG-COR-0004")
        article = self._make_article("detach-1")
        link = attach_article_to_signal(
            signal=signal, article=article, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            detach_article_from_signal(
                signal_article=link,
                actor=self.actor,
                reason="Tidak relevan lagi",
            )

    def test_detach_without_reason_rejected(self):
        signal = self._make_signal("SIG-COR-0005")
        article_a = self._make_article("detach-2a")
        article_b = self._make_article("detach-2b")
        attach_article_to_signal(
            signal=signal, article=article_a, actor=self.actor
        )
        link_b = attach_article_to_signal(
            signal=signal, article=article_b, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            detach_article_from_signal(
                signal_article=link_b, actor=self.actor, reason=""
            )

    def test_detach_succeeds_when_articles_remain(self):
        signal = self._make_signal("SIG-COR-0006")
        article_a = self._make_article("detach-3a")
        article_b = self._make_article("detach-3b")
        attach_article_to_signal(
            signal=signal, article=article_a, actor=self.actor
        )
        link_b = attach_article_to_signal(
            signal=signal, article=article_b, actor=self.actor
        )

        detach_article_from_signal(
            signal_article=link_b,
            actor=self.actor,
            reason="Duplikat berita lain",
        )

        self.assertEqual(signal.signal_articles.count(), 1)


class UpdateSupportTypeTests(SignalCorrelationFixture):
    def test_update_support_type_changes_value(self):
        signal = self._make_signal("SIG-COR-0010")
        article = self._make_article("support-1")
        link = attach_article_to_signal(
            signal=signal, article=article, actor=self.actor
        )

        updated = update_article_support_type(
            signal_article=link,
            support_type=SignalArticle.SupportType.CONTRADICTING,
            actor=self.actor,
        )

        self.assertEqual(
            updated.support_type, SignalArticle.SupportType.CONTRADICTING
        )

    def test_invalid_support_type_rejected(self):
        signal = self._make_signal("SIG-COR-0011")
        article = self._make_article("support-2")
        link = attach_article_to_signal(
            signal=signal, article=article, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            update_article_support_type(
                signal_article=link,
                support_type="bukan-jenis-valid",
                actor=self.actor,
            )


class MergeSignalsTests(SignalCorrelationFixture):
    def test_merge_moves_articles_and_closes_secondary(self):
        primary = self._make_signal("SIG-COR-0020")
        secondary = self._make_signal("SIG-COR-0021")
        article = self._make_article("merge-1")
        attach_article_to_signal(
            signal=secondary, article=article, actor=self.actor
        )

        merge_signals(
            primary_signal=primary,
            secondary_signal=secondary,
            actor=self.actor,
            reason="Kejadian yang sama, beda rentang tanggal.",
        )

        secondary.refresh_from_db()
        self.assertEqual(secondary.status, Signal.Status.CLOSED)
        self.assertEqual(primary.signal_articles.count(), 1)
        self.assertTrue(
            primary.signal_articles.filter(article=article).exists()
        )

    def test_merge_deduplicates_overlapping_articles(self):
        primary = self._make_signal("SIG-COR-0022")
        secondary = self._make_signal("SIG-COR-0023")
        shared_article = self._make_article("merge-shared")
        only_secondary_article = self._make_article("merge-only-secondary")

        attach_article_to_signal(
            signal=primary, article=shared_article, actor=self.actor
        )
        attach_article_to_signal(
            signal=secondary, article=shared_article, actor=self.actor
        )
        attach_article_to_signal(
            signal=secondary,
            article=only_secondary_article,
            actor=self.actor,
        )

        merge_signals(
            primary_signal=primary,
            secondary_signal=secondary,
            actor=self.actor,
            reason="Gabung, ada artikel yang sama di keduanya.",
        )

        # Artikel yang overlap tidak boleh dobel -- primary tetap
        # cuma punya 2 artikel unik (shared + only_secondary), bukan 3.
        self.assertEqual(primary.signal_articles.count(), 2)

    def test_merge_self_rejected(self):
        signal = self._make_signal("SIG-COR-0024")

        with self.assertRaises(ValidationError):
            merge_signals(
                primary_signal=signal,
                secondary_signal=signal,
                actor=self.actor,
                reason="Coba gabung diri sendiri.",
            )

    def test_merge_without_reason_rejected(self):
        primary = self._make_signal("SIG-COR-0025")
        secondary = self._make_signal("SIG-COR-0026")

        with self.assertRaises(ValidationError):
            merge_signals(
                primary_signal=primary,
                secondary_signal=secondary,
                actor=self.actor,
                reason="",
            )

    def test_merge_closed_signal_rejected(self):
        primary = self._make_signal("SIG-COR-0027")
        secondary = self._make_signal(
            "SIG-COR-0028", status=Signal.Status.CLOSED
        )

        with self.assertRaises(ValidationError):
            merge_signals(
                primary_signal=primary,
                secondary_signal=secondary,
                actor=self.actor,
                reason="Sinyal sekunder sudah ditutup.",
            )


class SplitSignalTests(SignalCorrelationFixture):
    def test_split_moves_selected_articles_to_new_signal(self):
        source_signal = self._make_signal("SIG-COR-0030")
        article_a = self._make_article("split-1a")
        article_b = self._make_article("split-1b")
        attach_article_to_signal(
            signal=source_signal, article=article_a, actor=self.actor
        )
        attach_article_to_signal(
            signal=source_signal, article=article_b, actor=self.actor
        )

        new_signal = split_signal(
            source_signal=source_signal,
            article_ids=[str(article_b.id)],
            actor=self.actor,
            new_title="Kejadian Terpisah",
            new_summary="Ternyata kejadian berbeda.",
            reason="Lokasi kejadian sebenarnya berbeda.",
        )

        self.assertEqual(source_signal.signal_articles.count(), 1)
        self.assertEqual(new_signal.signal_articles.count(), 1)
        self.assertEqual(new_signal.status, Signal.Status.NEEDS_REVIEW)
        self.assertEqual(
            new_signal.primary_disease, source_signal.primary_disease
        )

    def test_split_all_articles_rejected(self):
        source_signal = self._make_signal("SIG-COR-0031")
        article_a = self._make_article("split-2a")
        article_b = self._make_article("split-2b")
        attach_article_to_signal(
            signal=source_signal, article=article_a, actor=self.actor
        )
        attach_article_to_signal(
            signal=source_signal, article=article_b, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            split_signal(
                source_signal=source_signal,
                article_ids=[str(article_a.id), str(article_b.id)],
                actor=self.actor,
                new_title="Semua Dipisah",
                new_summary="Coba pisah semua.",
                reason="Ingin memisah semua artikel.",
            )

    def test_split_without_new_title_rejected(self):
        source_signal = self._make_signal("SIG-COR-0032")
        article_a = self._make_article("split-3a")
        article_b = self._make_article("split-3b")
        attach_article_to_signal(
            signal=source_signal, article=article_a, actor=self.actor
        )
        attach_article_to_signal(
            signal=source_signal, article=article_b, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            split_signal(
                source_signal=source_signal,
                article_ids=[str(article_b.id)],
                actor=self.actor,
                new_title="",
                new_summary="Ada ringkasan tapi judul kosong.",
                reason="Judul belum diisi.",
            )

    def test_split_empty_selection_rejected(self):
        source_signal = self._make_signal("SIG-COR-0033")
        article_a = self._make_article("split-4a")
        attach_article_to_signal(
            signal=source_signal, article=article_a, actor=self.actor
        )

        with self.assertRaises(ValidationError):
            split_signal(
                source_signal=source_signal,
                article_ids=[],
                actor=self.actor,
                new_title="Judul",
                new_summary="Ringkasan",
                reason="Tidak ada yang dipilih.",
            )
