from django.db import migrations

PROGRAM_CODE = "skdr-penyakit-menular"

DISEASES = [
    ("Demam Berdarah Dengue", "Dengue Hemorrhagic Fever", "demam-berdarah-dengue", "potensial_klb", ["DBD", "Dengue", "Demam Berdarah", "Demam Dengue"]),
    ("Diare Akut dan Kolera", "Acute Diarrhea and Cholera", "diare-akut-kolera", "potensial_klb", ["Diare Akut", "Diare", "Kolera", "Cholera"]),
    ("Campak dan Rubella", "Measles and Rubella", "campak-rubella", "potensial_klb", ["Campak", "Measles", "Rubella", "Campak Rubella"]),
    ("Difteri", "Diphtheria", "difteri", "potensial_klb", ["Difteri", "Diphtheria"]),
    ("Pertusis", "Pertussis", "pertusis", "potensial_klb", ["Pertusis", "Batuk Rejan", "Whooping Cough", "Pertussis"]),
    ("Polio dan Acute Flaccid Paralysis", "Poliomyelitis and Acute Flaccid Paralysis", "polio-afp", "potensial_klb", ["Polio", "Poliomyelitis", "AFP", "Acute Flaccid Paralysis", "Lumpuh Layuh Akut"]),
    ("Malaria", "Malaria", "malaria", "tular_vektor", ["Malaria"]),
    ("Rabies", "Rabies", "rabies", "zoonosis", ["Rabies", "GHPR", "Gigitan Hewan Penular Rabies"]),
    ("Flu Burung", "Avian Influenza", "flu-burung", "zoonosis", ["Flu Burung", "Avian Influenza", "AI", "H5N1", "H7N9"]),
    ("Leptospirosis", "Leptospirosis", "leptospirosis", "zoonosis", ["Leptospirosis", "Leptospira"]),
    ("Antraks", "Anthrax", "antraks", "zoonosis", ["Antraks", "Anthrax"]),
    ("Tuberkulosis", "Tuberculosis", "tuberkulosis", "menular_langsung", ["Tuberkulosis", "TBC", "TB", "Tuberculosis"]),
    ("HIV/AIDS dan Infeksi Menular Seksual", "HIV/AIDS and Sexually Transmitted Infections", "hiv-aids-ims", "menular_langsung", ["HIV", "AIDS", "HIV/AIDS", "IMS", "Infeksi Menular Seksual", "STI", "STD"]),
    ("Kusta", "Leprosy", "kusta", "menular_langsung", ["Kusta", "Lepra", "Leprosy", "Morbus Hansen"]),
    ("Hepatitis B", "Hepatitis B", "hepatitis-b", "menular_langsung", ["Hepatitis B", "HBV"]),
    ("Hepatitis C", "Hepatitis C", "hepatitis-c", "menular_langsung", ["Hepatitis C", "HCV"]),
    ("Filariasis", "Lymphatic Filariasis", "filariasis", "tular_vektor", ["Filariasis", "Kaki Gajah", "Lymphatic Filariasis"]),
    ("Chikungunya", "Chikungunya", "chikungunya", "tular_vektor", ["Chikungunya", "Chikungunya Fever"]),
    ("Schistosomiasis", "Schistosomiasis", "schistosomiasis", "tular_vektor", ["Schistosomiasis", "Demam Keong"]),
    ("SARS dan MERS", "Severe Acute Respiratory Syndrome and Middle East Respiratory Syndrome", "sars-mers", "emerging_reemerging", ["SARS", "SARS-CoV", "MERS", "MERS-CoV"]),
    ("COVID-19", "Coronavirus Disease 2019", "covid-19", "emerging_reemerging", ["COVID-19", "COVID 19", "Coronavirus Disease 2019", "SARS-CoV-2"]),
    ("Influenza Musiman", "Seasonal Influenza", "influenza-musiman", "emerging_reemerging", ["Influenza", "Flu", "Influenza Musiman", "H1N1", "H3N2", "Seasonal Influenza"]),
    ("Mpox", "Mpox", "mpox", "emerging_reemerging", ["Mpox", "Monkeypox", "Cacar Monyet"]),
    ("Penyakit Virus Nipah", "Nipah Virus Disease", "virus-nipah", "emerging_reemerging", ["Nipah", "Virus Nipah", "Nipah Virus", "Nipah Virus Disease"]),
    ("Ebola dan Marburg", "Ebola Virus Disease and Marburg Virus Disease", "ebola-marburg", "emerging_reemerging", ["Ebola", "Ebola Virus Disease", "EVD", "Marburg", "Marburg Virus Disease", "MVD"]),
    ("Penyakit Virus Zika", "Zika Virus Disease", "virus-zika", "emerging_reemerging", ["Zika", "Virus Zika", "Zika Virus", "Zika Virus Disease"]),
    ("Japanese Encephalitis", "Japanese Encephalitis", "japanese-encephalitis", "emerging_reemerging", ["Japanese Encephalitis", "JE", "Ensefalitis Jepang"]),
    ("Infeksi Rotavirus", "Rotavirus Infection", "rotavirus", "emerging_reemerging", ["Rotavirus", "Infeksi Rotavirus", "Rotavirus Infection"]),
]


def seed_full_surveillance_diseases(apps, schema_editor):
    Disease = apps.get_model("entities", "Disease")
    DiseaseAlias = apps.get_model("entities", "DiseaseAlias")
    SurveillanceProgram = apps.get_model("entities", "SurveillanceProgram")
    SurveillanceDisease = apps.get_model("entities", "SurveillanceDisease")

    program, _ = SurveillanceProgram.objects.update_or_create(
        code=PROGRAM_CODE,
        defaults={
            "name": "Program Surveilans Penyakit Menular",
            "legal_basis": "Permenkes Nomor 45 Tahun 2014 dan Permenkes Nomor 1501 Tahun 2010",
            "document_number": "45/2014; 1501/2010",
            "document_year": 2014,
            "status": "active",
            "notes": "Daftar operasional 28 kelompok penyakit untuk pengembangan dan penyaringan artikel OSINT MedIntel.",
        },
    )

    old_hiv = Disease.objects.filter(code="hiv-aids").first()
    if old_hiv and not Disease.objects.filter(code="hiv-aids-ims").exists():
        old_hiv.code = "hiv-aids-ims"
        old_hiv.name = "HIV/AIDS dan Infeksi Menular Seksual"
        old_hiv.canonical_name = "HIV/AIDS and Sexually Transmitted Infections"
        old_hiv.category = "menular_langsung"
        old_hiv.is_priority = True
        old_hiv.is_active = True
        old_hiv.save()

    for name, canonical_name, code, category, aliases in DISEASES:
        disease, _ = Disease.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "canonical_name": canonical_name,
                "category": category,
                "description": "",
                "is_priority": True,
                "is_active": True,
            },
        )

        for alias_text in aliases:
            DiseaseAlias.objects.update_or_create(
                disease=disease,
                alias=alias_text,
                defaults={"language": "id", "is_active": True},
            )

        SurveillanceDisease.objects.update_or_create(
            program=program,
            disease=disease,
            defaults={
                "official_name": name,
                "category": category,
                "is_active": True,
                "notes": "Termasuk daftar operasional program surveilans MedIntel.",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0006_seed_surveillance_baseline"),
    ]

    operations = [
        migrations.RunPython(
            seed_full_surveillance_diseases,
            migrations.RunPython.noop,
        ),
    ]
