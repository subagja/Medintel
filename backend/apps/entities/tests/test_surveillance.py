from django.test import TestCase

from apps.entities.models import (
    Disease,
    DiseaseAlias,
    SurveillanceDisease,
    SurveillanceProgram,
)
from apps.entities.surveillance import (
    match_text_to_surveillance,
)


class SurveillanceMatchingTests(TestCase):
    def setUp(self):
        self.program = (
            SurveillanceProgram.objects.create(
                name="Program Surveilans Uji",
                code="program-surveilans-uji",
                status=(
                    SurveillanceProgram.Status.ACTIVE
                ),
            )
        )

        self.disease = Disease.objects.create(
            name="Demam Berdarah Dengue",
            canonical_name="Dengue",
            code="dbd-surveillance-test",
            category="Penyakit Menular",
            is_priority=True,
            is_active=True,
        )

        DiseaseAlias.objects.create(
            disease=self.disease,
            alias="DBD",
            language="id",
            is_active=True,
        )

        SurveillanceDisease.objects.create(
            program=self.program,
            disease=self.disease,
            official_name="Demam Berdarah Dengue",
            category="Penyakit Menular",
            is_active=True,
        )

    def test_matches_disease_name(self):
        result = match_text_to_surveillance(
            (
                "Kasus Demam Berdarah Dengue "
                "meningkat di wilayah tersebut."
            )
        )

        self.assertTrue(
            result.is_relevant
        )

        self.assertEqual(
            result.matches[0].disease_name,
            "Demam Berdarah Dengue",
        )

    def test_matches_disease_alias(self):
        result = match_text_to_surveillance(
            "Dinas Kesehatan mencatat kasus DBD."
        )

        self.assertTrue(
            result.is_relevant
        )

        self.assertIn(
            "DBD",
            result.matches[0].matched_terms,
        )

    def test_rejects_unrelated_text(self):
        result = match_text_to_surveillance(
            "Pemerintah memperbaiki jalan kabupaten."
        )

        self.assertFalse(
            result.is_relevant
        )

    def test_inactive_membership_is_not_used(self):
        membership = (
            SurveillanceDisease.objects.get(
                program=self.program,
                disease=self.disease,
            )
        )

        membership.is_active = False
        membership.save(
            update_fields=[
                "is_active",
                "updated_at",
            ]
        )

        result = match_text_to_surveillance(
            "Kasus DBD meningkat."
        )

        self.assertFalse(
            result.is_relevant
        )