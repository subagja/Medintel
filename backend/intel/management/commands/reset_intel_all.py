from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.core.management.color import no_style

from intel.models import (
    Source,
    Location,
    LocationAlias,
    Signal,
    SignalLocation,
    ScoringRule,
    SystemSetting,
    Alert,
    AuditLog,
)


class Command(BaseCommand):
    help = "Menghapus seluruh data aplikasi intel dan mereset ID, tetapi tidak menghapus user/admin Django."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true")

    def handle(self, *args, **options):
        if not options["yes"]:
            confirm = input(
                "Ini akan menghapus SEMUA data intel dan mereset ID ke awal. Ketik YES: "
            )
            if confirm != "YES":
                self.stdout.write(self.style.ERROR("Dibatalkan."))
                return

        models_to_reset = [
            SignalLocation,
            Signal,
            Alert,
            AuditLog,
            LocationAlias,
            Location,
            ScoringRule,
            SystemSetting,
            Source,
        ]

        with transaction.atomic():
            counts = {
                "SignalLocation": SignalLocation.objects.count(),
                "Signal": Signal.objects.count(),
                "Alert": Alert.objects.count(),
                "AuditLog": AuditLog.objects.count(),
                "LocationAlias": LocationAlias.objects.count(),
                "Location": Location.objects.count(),
                "ScoringRule": ScoringRule.objects.count(),
                "SystemSetting": SystemSetting.objects.count(),
                "Source": Source.objects.count(),
            }

            # Hapus data dengan urutan aman berdasarkan relasi FK
            SignalLocation.objects.all().delete()
            Signal.objects.all().delete()
            Alert.objects.all().delete()
            AuditLog.objects.all().delete()
            LocationAlias.objects.all().delete()
            Location.objects.all().delete()
            ScoringRule.objects.all().delete()
            SystemSetting.objects.all().delete()
            Source.objects.all().delete()

            # Reset auto-increment / sequence ID
            sequence_sql = connection.ops.sequence_reset_sql(no_style(), models_to_reset)

            with connection.cursor() as cursor:
                if sequence_sql:
                    for sql in sequence_sql:
                        cursor.execute(sql)

                # Tambahan khusus SQLite supaya ID benar-benar balik dari 1
                if connection.vendor == "sqlite":
                    table_names = [model._meta.db_table for model in models_to_reset]

                    for table in table_names:
                        cursor.execute(
                            "DELETE FROM sqlite_sequence WHERE name = %s",
                            [table],
                        )

        self.stdout.write(self.style.SUCCESS("Reset semua data intel selesai."))
        for name, count in counts.items():
            self.stdout.write(f"{name}: deleted {count}")

        self.stdout.write(self.style.SUCCESS("Auto-increment ID berhasil direset."))