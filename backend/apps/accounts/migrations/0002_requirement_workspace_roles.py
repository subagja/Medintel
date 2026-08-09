from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


REQUIREMENT_MODELS = (
    "intelligencerequirement",
    "requirementkeyword",
    "requirementdisease",
    "requirementlocation",
    "requirementindicator",
    "requirementcollectionsession",
    "requirementarticle",
    "requirementinformationgap",
    "requirementhistory",
)


def refresh_requirement_workspace_roles(apps, schema_editor):
    app_config = global_apps.get_app_config("requirements")
    create_permissions(app_config, verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    content_types = ContentType.objects.filter(
        app_label="requirements",
        model__in=REQUIREMENT_MODELS,
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


def reverse_requirement_workspace_roles(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_types = ContentType.objects.filter(
        app_label="requirements",
        model__in=REQUIREMENT_MODELS,
    )
    Permission.objects.filter(content_type__in=content_types).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_setup_roles"),
        ("requirements", "0003_requirementhistory_requirementinformationgap_and_more"),
    ]

    operations = [
        migrations.RunPython(
            refresh_requirement_workspace_roles,
            reverse_requirement_workspace_roles,
        ),
    ]

