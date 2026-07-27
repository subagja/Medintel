from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection, transaction

from intel.models import (
    Alert,
    AuditLog,
    ResolvedSourceURL,
    Signal,
    SignalCluster,
    SignalLocation,
    Source,
)


class Command(BaseCommand):
    help = "Reset operational crawling data while keeping reference/master data."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true")
        parser.add_argument(
            "--keep-sources",
            action="store_true",
            help="Keep Source rows. By default Source rows are also reset.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            confirm = input(
                "Ini akan menghapus data operasional intel: signal, cluster, alert, audit log, URL cache"
                " dan source. Master data seperti DiseaseMaster, Location, alias, scoring rules,"
                " dan system setting tetap disimpan. Ketik YES: "
            )
            if confirm != "YES":
                self.stdout.write(self.style.ERROR("Dibatalkan."))
                return

        models_to_reset = [
            SignalLocation,
            Signal,
            SignalCluster,
            Alert,
            AuditLog,
            ResolvedSourceURL,
        ]

        if not options["keep_sources"]:
            models_to_reset.append(Source)

        counts = {model.__name__: model.objects.count() for model in models_to_reset}

        with transaction.atomic():
            SignalLocation.objects.all().delete()
            Signal.objects.all().delete()
            SignalCluster.objects.all().delete()
            Alert.objects.all().delete()
            AuditLog.objects.all().delete()
            ResolvedSourceURL.objects.all().delete()

            if not options["keep_sources"]:
                Source.objects.all().delete()

            sequence_sql = connection.ops.sequence_reset_sql(no_style(), models_to_reset)
            with connection.cursor() as cursor:
                if sequence_sql:
                    for sql in sequence_sql:
                        cursor.execute(sql)

                if connection.vendor == "sqlite":
                    table_names = [model._meta.db_table for model in models_to_reset]
                    for table in table_names:
                        cursor.execute(
                            "DELETE FROM sqlite_sequence WHERE name = %s",
                            [table],
                        )

        self.stdout.write(self.style.SUCCESS("Reset data operasional selesai."))
        for name, count in counts.items():
            self.stdout.write(f"{name}: deleted {count}")

        self.stdout.write("")
        self.stdout.write("Reference/master data yang dipertahankan:")
        self.stdout.write("- DiseaseMaster")
        self.stdout.write("- Location dan LocationAlias")
        self.stdout.write("- PublisherDomainAlias")
        self.stdout.write("- ScoringRule")
        self.stdout.write("- SystemSetting")
