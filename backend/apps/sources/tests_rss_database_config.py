from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import Source, SourceSeedUrl, SourceUrlPattern


class OfficialRssDatabaseConfigTests(TestCase):
    def setUp(self):
        self.cdc = self._source(
            code="cdc-us",
            name="US CDC",
            domain="cdc.gov",
            base_url="https://www.cdc.gov",
        )
        self.fajar = self._source(
            code="fajar",
            name="Fajar",
            domain="fajaronline.co.id",
            base_url="https://fajaronline.co.id",
            crawl_enabled=True,
        )
        self.old_fajar_seed = SourceSeedUrl.objects.create(
            source=self.fajar,
            url="https://fajaronline.co.id/berita/",
            seed_type=SourceSeedUrl.SeedType.LISTING,
            is_active=True,
        )
        self._source(
            code="who",
            name="World Health Organization",
            domain="who.int",
            base_url="https://www.who.int",
        )
        self._source(
            code="detik",
            name="Detikcom",
            domain="detik.com",
            base_url="https://www.detik.com",
        )
        self._source(
            code="liputan6",
            name="Liputan6",
            domain="liputan6.com",
            base_url="https://www.liputan6.com",
        )
        self._source(
            code="tempo",
            name="Tempo.co",
            domain="tempo.co",
            base_url="https://www.tempo.co",
        )
        self._source(
            code="republika",
            name="Republika",
            domain="republika.co.id",
            base_url="https://republika.co.id",
        )
        self._source(
            code="tribunnews",
            name="Tribunnews",
            domain="tribunnews.com",
            base_url="https://www.tribunnews.com",
        )
        self._source(
            code="cenderawasih-pos",
            name="Cenderawasih Pos",
            domain="ceposonline.com",
            base_url="https://www.ceposonline.com",
        )
        self._source(
            code="surya",
            name="Surya",
            domain="surya.co.id",
            base_url="https://surya.co.id",
        )

    @staticmethod
    def _source(
        *,
        code: str,
        name: str,
        domain: str,
        base_url: str,
        crawl_enabled: bool = False,
    ) -> Source:
        return Source.objects.create(
            name=name,
            code=code,
            domain=domain,
            base_url=base_url,
            source_type=Source.SourceType.NATIONAL_MEDIA,
            is_verified=True,
            is_active=True,
            crawl_enabled=crawl_enabled,
            request_delay_seconds=0,
        )

    def test_dry_run_does_not_change_database(self):
        call_command(
            "prepare_official_rss_sources",
            "--source",
            "cdc-us",
            stdout=StringIO(),
        )

        self.cdc.refresh_from_db()
        self.assertFalse(self.cdc.allow_subdomains)
        self.assertFalse(
            self.cdc.url_patterns.filter(pattern=r"^/eid/article/").exists()
        )

    def test_apply_updates_subdomain_and_allow_pattern(self):
        call_command(
            "prepare_official_rss_sources",
            "--source",
            "cdc-us",
            "--apply",
            stdout=StringIO(),
        )

        self.cdc.refresh_from_db()
        self.assertTrue(self.cdc.allow_subdomains)
        pattern = self.cdc.url_patterns.get(pattern=r"^/eid/article/")
        self.assertEqual(
            pattern.pattern_type,
            SourceUrlPattern.PatternType.ALLOW,
        )
        self.assertTrue(pattern.is_active)

    def test_domain_replacement_disables_old_seed_and_crawling(self):
        call_command(
            "prepare_official_rss_sources",
            "--source",
            "fajar",
            "--apply",
            stdout=StringIO(),
        )

        self.fajar.refresh_from_db()
        self.old_fajar_seed.refresh_from_db()
        self.assertEqual(self.fajar.domain, "fajar.co.id")
        self.assertEqual(self.fajar.base_url, "https://fajar.co.id")
        self.assertFalse(self.fajar.crawl_enabled)
        self.assertFalse(self.old_fajar_seed.is_active)

    def test_apply_is_idempotent(self):
        for _ in range(2):
            call_command(
                "prepare_official_rss_sources",
                "--source",
                "cdc-us",
                "--apply",
                stdout=StringIO(),
            )

        self.assertEqual(
            self.cdc.url_patterns.filter(pattern=r"^/eid/article/").count(),
            1,
        )

    def test_apply_all_curated_database_patches(self):
        call_command(
            "prepare_official_rss_sources",
            "--apply",
            stdout=StringIO(),
        )

        for code in (
            "cdc-us",
            "who",
            "detik",
            "liputan6",
            "tempo",
            "republika",
        ):
            self.assertTrue(Source.objects.get(code=code).allow_subdomains)

        expected_domains = {
            "fajar": "fajar.co.id",
            "cenderawasih-pos": "cenderawasihpos.jawapos.com",
            "surya": "surabaya.tribunnews.com",
        }
        for code, domain in expected_domains.items():
            self.assertEqual(Source.objects.get(code=code).domain, domain)

        self.assertTrue(
            SourceUrlPattern.objects.filter(
                source__code="tribunnews",
                pattern=r"/[0-9]{4}/[0-9]{2}/[0-9]{1,2}/",
                is_active=True,
            ).exists()
        )

    def test_status_verification_is_not_changed(self):
        self.cdc.is_active = False
        self.cdc.is_verified = False
        self.cdc.save()

        call_command(
            "prepare_official_rss_sources",
            "--source",
            "cdc-us",
            "--apply",
            stdout=StringIO(),
        )

        self.cdc.refresh_from_db()
        self.assertFalse(self.cdc.is_active)
        self.assertFalse(self.cdc.is_verified)
        self.assertFalse(self.cdc.crawl_enabled)
