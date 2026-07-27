from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


DEFAULT_GROUPS = [
    "Admin Sistem",
    "Analis Senior",
    "Analis",
    "Viewer",
]


class Command(BaseCommand):
    help = "Create default user roles/groups"

    def handle(self, *args, **options):
        created = 0

        for name in DEFAULT_GROUPS:
            _, was_created = Group.objects.get_or_create(name=name)
            if was_created:
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Role seed selesai. Group baru dibuat: {created}"))