from django.db import migrations

PROGRAM_CODE = "skdr-penyakit-menular"

DISEASES = [
    {
        "name": "Tuberkulosis",
        "canonical_name": "Tuberculosis",
        "code": "tuberkulosis",
        "category": "menular_langsung",
        "aliases": ["Tuberkulosis", "TBC", "TB", "Tuberculosis"],
        "official_name": "Tuberkulosis",
    },
    {
        "name": "Malaria",
        "canonical_name": "Malaria",
        "code": "malaria",
        "category": "tular_vektor",
        "aliases": ["Malaria"],
        "official_name": "Malaria",
    },
    {
        "name": "HIV/AIDS",
        "canonical_name": "Human Immunodeficiency Virus / Acquired Immunodeficiency Syndrome",
        "code": "hiv-aids",
        "category": "menular_langsung",
        "aliases": ["HIV", "AIDS", "HIV/AIDS"],
        "official_name": "HIV/AIDS",
    },
]


def seed_surveillance_baseline(apps, schema_editor):
    Disease = apps.get_model("entities", "Disease")
    DiseaseAlias = apps.get_model("entities", "DiseaseAlias")
    SurveillanceProgram = apps.get_model("entities", "SurveillanceProgram")
    SurveillanceDisease = apps.get_model("entities", "SurveillanceDisease")

    program, _ = SurveillanceProgram.objects.update_or_create(
        code=PROGRAM_CODE,
        defaults={
            "name": "Program Surveilans Penyakit Menular",
            "legal_basis": (
                "Permenkes Nomor 45 Tahun 2014 dan "
                "Permenkes Nomor 1501 Tahun 2010"
            ),
            "document_number": "45/2014; 1501/2010",
            "document_year": 2014,
            "status": "active",
            "notes": (
                "Baseline development MedIntel untuk penyaringan "
                "artikel OSINT penyakit menular."
            ),
        },
    )

    for item in DISEASES:
        disease, _ = Disease.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "canonical_name": item["canonical_name"],
                "category": item["category"],
                "description": "",
                "is_priority": True,
                "is_active": True,
            },
        )

        for alias_text in item["aliases"]:
            DiseaseAlias.objects.update_or_create(
                disease=disease,
                alias=alias_text,
                defaults={
                    "language": "id",
                    "is_active": True,
                },
            )

        SurveillanceDisease.objects.update_or_create(
            program=program,
            disease=disease,
            defaults={
                "official_name": item["official_name"],
                "category": item["category"],
                "is_active": True,
                "notes": "Baseline development MedIntel.",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0005_surveillanceprogram_surveillancedisease"),
    ]

    operations = [
        migrations.RunPython(
            seed_surveillance_baseline,
            migrations.RunPython.noop,
        ),
    ]
