from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def add_collection_queue_permissions(apps, schema_editor):
    app_config = global_apps.get_app_config("collection")
    create_permissions(app_config, verbosity=0)
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_types = ContentType.objects.filter(app_label="collection")
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
    reviewer.permissions.add(*view_permissions)
    viewer.permissions.add(*view_permissions)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_quality_evaluation_roles"),
        ("collection", "0005_persistent_queue_schedule_worker"),
    ]

    operations = [
        migrations.RunPython(
            add_collection_queue_permissions,
            migrations.RunPython.noop,
        ),
    ]
