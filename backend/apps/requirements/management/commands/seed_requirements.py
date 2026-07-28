from django.core.management.base import BaseCommand

from apps.requirements.models import (
    IntelligenceRequirement,
    RequirementKeyword,
)


REQUIREMENTS = [
    {
        "code": "ir-001",
        "title": (
            "Indikasi peningkatan penyakit menular "
            "berpotensi KLB"
        ),
        "description": (
            "Memantau indikasi peningkatan kasus, "
            "kematian, atau kejadian baru penyakit "
            "menular yang berpotensi berkembang menjadi "
            "kejadian luar biasa."
        ),
        "requirement_type": (
            IntelligenceRequirement.RequirementType.DISEASE_EVENT
        ),
        "priority": IntelligenceRequirement.Priority.HIGH,
        "keywords": [
            (
                "peningkatan kasus",
                RequirementKeyword.KeywordType.TREND,
                3.0,
            ),
            (
                "melonjak",
                RequirementKeyword.KeywordType.TREND,
                2.0,
            ),
            (
                "kematian",
                RequirementKeyword.KeywordType.IMPACT,
                3.0,
            ),
            (
                "kejadian luar biasa",
                RequirementKeyword.KeywordType.ANOMALY,
                3.0,
            ),
        ],
    },
    {
        "code": "ir-002",
        "title": (
            "Indikasi penyebaran penyakit "
            "lintas wilayah"
        ),
        "description": (
            "Memantau indikasi perluasan penyakit "
            "ke kabupaten, kota, provinsi, atau wilayah baru."
        ),
        "requirement_type": (
            IntelligenceRequirement.RequirementType.GEOGRAPHIC_SPREAD
        ),
        "priority": IntelligenceRequirement.Priority.HIGH,
        "keywords": [
            (
                "menyebar",
                RequirementKeyword.KeywordType.TREND,
                3.0,
            ),
            (
                "meluas",
                RequirementKeyword.KeywordType.TREND,
                3.0,
            ),
            (
                "wilayah baru",
                RequirementKeyword.KeywordType.LOCATION,
                3.0,
            ),
        ],
    },
    {
        "code": "ir-003",
        "title": (
            "Indikasi dampak serius penyakit menular"
        ),
        "description": (
            "Memantau kejadian penyakit menular "
            "yang menimbulkan kematian atau dampak "
            "kesehatan masyarakat yang signifikan."
        ),
        "requirement_type": (
            IntelligenceRequirement.RequirementType.IMPACT
        ),
        "priority": IntelligenceRequirement.Priority.CRITICAL,
        "keywords": [
            (
                "meninggal",
                RequirementKeyword.KeywordType.IMPACT,
                3.0,
            ),
            (
                "kematian",
                RequirementKeyword.KeywordType.IMPACT,
                3.0,
            ),
            (
                "dirawat",
                RequirementKeyword.KeywordType.IMPACT,
                2.0,
            ),
        ],
    },
]


class Command(BaseCommand):
    help = "Mengisi kebutuhan intelijen awal MedIntel."

    def handle(self, *args, **options):
        requirement_count = 0
        keyword_count = 0

        for requirement_data in REQUIREMENTS:
            data = requirement_data.copy()
            keywords = data.pop("keywords")

            requirement, _ = (
                IntelligenceRequirement.objects.update_or_create(
                    code=data["code"],
                    defaults=data,
                )
            )

            requirement_count += 1

            for keyword, keyword_type, weight in keywords:
                RequirementKeyword.objects.update_or_create(
                    requirement=requirement,
                    keyword=keyword,
                    defaults={
                        "keyword_type": keyword_type,
                        "weight": weight,
                        "is_active": True,
                    },
                )

                keyword_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Seed kebutuhan intelijen selesai | "
                    f"kebutuhan={requirement_count} | "
                    f"kata kunci={keyword_count}"
                )
            )
        )