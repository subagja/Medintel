from dataclasses import dataclass, field

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.entities.models import (
    Disease,
    DiseaseAlias,
    SurveillanceDisease,
    SurveillanceProgram,
)


@dataclass(frozen=True)
class DiseaseSeed:
    name: str
    code: str
    category: str
    canonical_name: str = ""
    aliases: tuple[str, ...] = field(
        default_factory=tuple
    )


SURVEILLANCE_DISEASES = (
    DiseaseSeed(
        name="Demam Berdarah Dengue",
        code="demam-berdarah-dengue",
        canonical_name="Dengue",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "DBD",
            "Dengue",
            "Demam Dengue",
        ),
    ),
    DiseaseSeed(
        name="Diare Akut",
        code="diare-akut",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Diare",
            "Acute Diarrhea",
        ),
    ),
    DiseaseSeed(
        name="Kolera",
        code="kolera",
        canonical_name="Cholera",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Cholera",
        ),
    ),
    DiseaseSeed(
        name="Campak",
        code="campak",
        canonical_name="Measles",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Measles",
        ),
    ),
    DiseaseSeed(
        name="Rubella",
        code="rubella",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(),
    ),
    DiseaseSeed(
        name="Difteri",
        code="difteri",
        canonical_name="Diphtheria",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Diphtheria",
        ),
    ),
    DiseaseSeed(
        name="Pertusis",
        code="pertusis",
        canonical_name="Pertussis",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Pertussis",
            "Batuk Rejan",
        ),
    ),
    DiseaseSeed(
        name="Polio",
        code="polio",
        canonical_name="Poliomyelitis",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Poliomyelitis",
        ),
    ),
    DiseaseSeed(
        name="Acute Flaccid Paralysis",
        code="acute-flaccid-paralysis",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "AFP",
            "Lumpuh Layuh Akut",
        ),
    ),
    DiseaseSeed(
        name="Malaria",
        code="malaria",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(),
    ),
    DiseaseSeed(
        name="Rabies",
        code="rabies",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(),
    ),
    DiseaseSeed(
        name="Gigitan Hewan Penular Rabies",
        code="gigitan-hewan-penular-rabies",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "GHPR",
        ),
    ),
    DiseaseSeed(
        name="Flu Burung",
        code="flu-burung",
        canonical_name="Avian Influenza",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Avian Influenza",
        ),
    ),
    DiseaseSeed(
        name="Leptospirosis",
        code="leptospirosis",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(),
    ),
    DiseaseSeed(
        name="Antraks",
        code="antraks",
        canonical_name="Anthrax",
        category="Penyakit Menular Potensial Wabah/KLB",
        aliases=(
            "Anthrax",
        ),
    ),
    DiseaseSeed(
        name="Tuberkulosis",
        code="tuberkulosis",
        canonical_name="Tuberculosis",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "TB",
            "TBC",
            "Tuberculosis",
        ),
    ),
    DiseaseSeed(
        name="HIV/AIDS",
        code="hiv-aids",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "HIV",
            "AIDS",
        ),
    ),
    DiseaseSeed(
        name="Infeksi Menular Seksual",
        code="infeksi-menular-seksual",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "IMS",
        ),
    ),
    DiseaseSeed(
        name="Kusta",
        code="kusta",
        canonical_name="Leprosy",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "Lepra",
            "Leprosy",
        ),
    ),
    DiseaseSeed(
        name="Hepatitis B",
        code="hepatitis-b",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "HBV",
        ),
    ),
    DiseaseSeed(
        name="Hepatitis C",
        code="hepatitis-c",
        category="Penyakit Menular Langsung dan Menahun",
        aliases=(
            "HCV",
        ),
    ),
    DiseaseSeed(
        name="Filariasis",
        code="filariasis",
        category="Penyakit Tular Vektor dan Zoonosis",
        aliases=(
            "Kaki Gajah",
        ),
    ),
    DiseaseSeed(
        name="Chikungunya",
        code="chikungunya",
        category="Penyakit Tular Vektor dan Zoonosis",
        aliases=(),
    ),
    DiseaseSeed(
        name="Schistosomiasis",
        code="schistosomiasis",
        category="Penyakit Tular Vektor dan Zoonosis",
        aliases=(),
    ),
    DiseaseSeed(
        name="COVID-19",
        code="covid-19",
        category="Penyakit Infeksi Emerging dan Re-Emerging",
        aliases=(
            "COVID 19",
            "Coronavirus Disease 2019",
        ),
    ),
    DiseaseSeed(
        name="Mpox",
        code="mpox",
        category="Penyakit Infeksi Emerging dan Re-Emerging",
        aliases=(
            "Cacar Monyet",
            "Monkeypox",
        ),
    ),
    DiseaseSeed(
        name="MERS-CoV",
        code="mers-cov",
        canonical_name=(
            "Middle East Respiratory Syndrome Coronavirus"
        ),
        category="Penyakit Infeksi Emerging dan Re-Emerging",
        aliases=(
            "MERS",
            "Middle East Respiratory Syndrome",
        ),
    ),
    DiseaseSeed(
        name="Ebola",
        code="ebola",
        canonical_name="Ebola Virus Disease",
        category="Penyakit Infeksi Emerging dan Re-Emerging",
        aliases=(
            "Ebola Virus Disease",
        ),
    ),
    DiseaseSeed(
        name="Influenza A Baru",
        code="influenza-a-baru",
        category="Penyakit Infeksi Emerging dan Re-Emerging",
        aliases=(
            "Novel Influenza A",
            "H5N1",
            "H7N9",
        ),
    ),
)


class Command(BaseCommand):
    help = (
        "Mengisi daftar kerja penyakit menular "
        "untuk surveilans MedIntel."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        program, program_created = (
            SurveillanceProgram.objects.update_or_create(
                code="medintel-surveilans-penyakit-menular",
                defaults={
                    "name": (
                        "Daftar Kerja Surveilans "
                        "Penyakit Menular MedIntel"
                    ),
                    "legal_basis": (
                        "Rangkuman cakupan surveilans "
                        "Kementerian Kesehatan yang "
                        "diberikan untuk pengembangan MedIntel"
                    ),
                    "document_number": "",
                    "document_year": None,
                    "status": (
                        SurveillanceProgram.Status.ACTIVE
                    ),
                    "notes": (
                        "Daftar operasional awal untuk "
                        "pembatasan crawling penyakit menular. "
                        "Tidak mencakup penyakit tidak menular."
                    ),
                },
            )
        )

        disease_created_count = 0
        disease_updated_count = 0
        alias_created_count = 0
        membership_created_count = 0
        membership_updated_count = 0

        for item in SURVEILLANCE_DISEASES:
            disease, disease_created = (
                Disease.objects.update_or_create(
                    code=item.code,
                    defaults={
                        "name": item.name,
                        "canonical_name": (
                            item.canonical_name
                        ),
                        "category": item.category,
                        "is_priority": True,
                        "is_active": True,
                    },
                )
            )

            if disease_created:
                disease_created_count += 1
            else:
                disease_updated_count += 1

            alias_values = set(item.aliases)

            if item.canonical_name:
                alias_values.add(
                    item.canonical_name
                )

            for alias in sorted(alias_values):
                normalized_alias = alias.strip()

                if not normalized_alias:
                    continue

                _, alias_created = (
                    DiseaseAlias.objects.get_or_create(
                        disease=disease,
                        alias=normalized_alias,
                        defaults={
                            "language": (
                                "en"
                                if normalized_alias
                                != item.name
                                else "id"
                            ),
                            "is_active": True,
                        },
                    )
                )

                if alias_created:
                    alias_created_count += 1

            membership, membership_created = (
                SurveillanceDisease.objects.update_or_create(
                    program=program,
                    disease=disease,
                    defaults={
                        "official_name": item.name,
                        "category": item.category,
                        "is_active": True,
                        "notes": (
                            "Termasuk dalam daftar kerja "
                            "surveilans penyakit menular."
                        ),
                    },
                )
            )

            if membership_created:
                membership_created_count += 1
            else:
                membership_updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Seed penyakit surveilans selesai."
            )
        )

        self.stdout.write(
            (
                f"Program baru           : "
                f"{1 if program_created else 0}"
            )
        )
        self.stdout.write(
            (
                f"Penyakit baru          : "
                f"{disease_created_count}"
            )
        )
        self.stdout.write(
            (
                f"Penyakit diperbarui    : "
                f"{disease_updated_count}"
            )
        )
        self.stdout.write(
            (
                f"Alias baru             : "
                f"{alias_created_count}"
            )
        )
        self.stdout.write(
            (
                f"Membership baru        : "
                f"{membership_created_count}"
            )
        )
        self.stdout.write(
            (
                f"Membership diperbarui  : "
                f"{membership_updated_count}"
            )
        )