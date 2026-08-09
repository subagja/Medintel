from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


QUALITY_MODELS = (
    "qualitymetricsnapshot",
    "evaluationrecord",
    "evaluationhistory",
)


def add_quality_permissions(apps, schema_editor):
    app_config = global_apps.get_app_config("quality")
    create_permissions(app_config, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_types = ContentType.objects.filter(
        app_label="quality", model__in=QUALITY_MODELS
    )
    permissions = Permission.objects.filter(content_type__in=content_types)
    view_permissions = permissions.filter(codename__startswith="view_")
    contributor_permissions = permissions.filter(
        codename__regex=r"^(view|add|change)_"
    )

    admin, _ = Group.objects.get_or_create(name="Admin")
    analyst, _ = Group.objects.get_or_create(name="Analyst")
    reviewer, _ = Group.objects.get_or_create(name="Reviewer")
    viewer, _ = Group.objects.get_or_create(name="Viewer")

    admin.permissions.add(*permissions)
    analyst.permissions.add(*contributor_permissions)
    reviewer.permissions.add(*contributor_permissions)
    viewer.permissions.add(*view_permissions)


def remove_quality_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_types = ContentType.objects.filter(
        app_label="quality", model__in=QUALITY_MODELS
    )
    permissions = list(Permission.objects.filter(content_type__in=content_types))
    for group in Group.objects.filter(
        name__in=["Admin", "Analyst", "Reviewer", "Viewer"]
    ):
        group.permissions.remove(*permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_requirement_workspace_roles"),
        ("quality", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_quality_permissions, remove_quality_permissions),
    ]
