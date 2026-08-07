# Data migration: setup 4 role dasar MedIntel sebagai Django Group, dengan
# permission bawaan (add/change/delete/view) yang Django buat otomatis untuk
# tiap model saat `migrate` dijalankan.
#
# Role:
#   - Admin     : akses penuh ke semua model (termasuk kelola user & sumber).
#                 Untuk masuk ke /admin/ tetap butuh is_staff=True terpisah;
#                 group ini hanya mengatur permission level aplikasi.
#   - Analyst   : kerja ekstraksi & validasi harian (artikel, entitas,
#                 indikator, pembentukan sinyal, jalankan crawl).
#   - Reviewer  : approval/eskalasi hasil kerja Analyst (sinyal, indikator,
#                 asesmen, early warning, rekomendasi intelijen).
#   - Viewer    : baca-baca saja, semua model, tanpa hak ubah.
#
# Catatan: migration ini idempotent (aman dijalankan ulang) karena pakai
# get_or_create untuk group dan permission di-assign lewat set(), bukan add
# berulang yang bisa duplikat.

from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


# (app_label, [ModelName, ...])
ALL_MODELS = {
    "sources": ["Source", "SourceUrlPattern", "SourceSeedUrl"],
    "articles": ["Article"],
    "collection": ["CollectionJob", "CollectionJobItem"],
    "entities": [
        "Disease",
        "DiseaseAlias",
        "ArticleDisease",
        "ArticleLocation",
        "ArticleFact",
        "ExtractionReviewLog",
        "Symptom",
        "SymptomAlias",
        "DiseaseSymptom",
        "SurveillanceProgram",
        "SurveillanceDisease",
    ],
    "locations": ["Location", "LocationAlias"],
    "signals": [
        "Signal",
        "SignalIndicator",
        "SignalArticle",
        "SignalRequirement",
        "SignalDisease",
        "SignalLocation",
        "SignalHistory",
    ],
    "indicators": [
        "IndicatorType",
        "Indicator",
        "IndicatorEvidence",
        "IndicatorReviewLog",
    ],
    "requirements": [
        "IntelligenceRequirement",
        "RequirementKeyword",
        "RequirementDisease",
        "RequirementLocation",
        "RequirementIndicator",
    ],
    "assessments": [
        "ArticleValidationAssessment",
        "ArticleValidationHistory",
        "SourceEvaluation",
        "InformationEvaluation",
        "SignalAssessment",
        "EarlyWarning",
        "EarlyWarningHistory",
        "IntelligenceRecommendation",
        "IntelligenceRecommendationHistory",
        "InformationGap",
    ],
}

# Model-model yang jadi fokus kerja harian Analyst: boleh tambah & ubah.
ANALYST_WRITE_MODELS = {
    "entities": [
        "ArticleDisease",
        "ArticleLocation",
        "ArticleFact",
        "ExtractionReviewLog",
    ],
    "collection": ["CollectionJob", "CollectionJobItem"],
    "assessments": ["ArticleValidationAssessment", "ArticleValidationHistory"],
    "indicators": ["Indicator", "IndicatorEvidence", "IndicatorReviewLog"],
    "signals": [
        "Signal",
        "SignalIndicator",
        "SignalArticle",
        "SignalDisease",
        "SignalLocation",
        "SignalHistory",
    ],
}

# Model-model yang jadi fokus approval/eskalasi Reviewer: boleh tambah & ubah
# (approve/reject/escalate berupa perubahan status, jadi cukup "change").
REVIEWER_WRITE_MODELS = {
    "entities": [
        "ArticleDisease",
        "ArticleLocation",
        "ArticleFact",
        "ExtractionReviewLog",
    ],
    "signals": ["Signal", "SignalHistory"],
    "indicators": ["Indicator", "IndicatorReviewLog"],
    "requirements": [
        "IntelligenceRequirement",
        "RequirementKeyword",
        "RequirementDisease",
        "RequirementLocation",
        "RequirementIndicator",
    ],
    "assessments": [
        "SourceEvaluation",
        "InformationEvaluation",
        "SignalAssessment",
        "EarlyWarning",
        "EarlyWarningHistory",
        "IntelligenceRecommendation",
        "IntelligenceRecommendationHistory",
        "InformationGap",
    ],
}


def _get_permissions(apps, app_label, model_name, actions):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    try:
        ct = ContentType.objects.get(app_label=app_label, model=model_name.lower())
    except ContentType.DoesNotExist:
        return []
    codenames = [f"{action}_{model_name.lower()}" for action in actions]
    return list(Permission.objects.filter(content_type=ct, codename__in=codenames))


def _all_permissions(apps, model_map, actions):
    perms = []
    for app_label, model_names in model_map.items():
        for model_name in model_names:
            perms.extend(_get_permissions(apps, app_label, model_name, actions))
    return perms


def create_roles(apps, schema_editor):
    # Permission bawaan Django (add/change/delete/view per model) normalnya
    # baru dibuat oleh sinyal post_migrate SETELAH seluruh proses `migrate`
    # selesai. Karena migration ini butuh permission itu SEKARANG (di
    # tengah proses migrate), kita paksa pembuatannya lebih awal di sini.
    for app_config in global_apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model("auth", "Group")

    admin_group, _ = Group.objects.get_or_create(name="Admin")
    analyst_group, _ = Group.objects.get_or_create(name="Analyst")
    reviewer_group, _ = Group.objects.get_or_create(name="Reviewer")
    viewer_group, _ = Group.objects.get_or_create(name="Viewer")

    view_perms = _all_permissions(apps, ALL_MODELS, ["view"])
    admin_perms = _all_permissions(apps, ALL_MODELS, ["add", "change", "delete", "view"])
    analyst_perms = _all_permissions(apps, ANALYST_WRITE_MODELS, ["add", "change"])
    reviewer_perms = _all_permissions(apps, REVIEWER_WRITE_MODELS, ["add", "change"])

    admin_group.permissions.set(admin_perms)
    analyst_group.permissions.set(analyst_perms + view_perms)
    reviewer_group.permissions.set(reviewer_perms + view_perms)
    viewer_group.permissions.set(view_perms)


def remove_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["Admin", "Analyst", "Reviewer", "Viewer"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0001_initial"),
        # Wajib eksplisit: memastikan migration bawaan Django yang
        # menghapus kolom lama `name` dari django_content_type sudah
        # jalan SEBELUM create_permissions() dipanggil di bawah. Tanpa
        # dependency ini, Django tidak menjamin urutannya -- migration
        # ini bisa saja dijalankan lebih dulu (tergantung urutan
        # topological sort di environment masing-masing), sehingga
        # create_contenttypes() masih menemukan skema tabel lama dan
        # gagal dengan NotNullViolation pada kolom "name".
        ("contenttypes", "0002_remove_content_type_name"),
        ("sources", "0002_sourceseedurl_alter_sourceurlpattern_options_and_more"),
        ("articles", "0003_alter_article_locations_app"),
        ("collection", "0001_initial"),
        ("entities", "0009_remove_location_state"),
        ("locations", "0001_move_location_from_entities"),
        ("signals", "0003_alter_location_app"),
        ("indicators", "0003_alter_indicator_location_app"),
        ("requirements", "0002_alter_location_app"),
        ("assessments", "0005_intelligencerecommendation_and_more"),
    ]

    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
