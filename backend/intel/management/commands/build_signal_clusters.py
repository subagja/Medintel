# backend/intel/management/commands/build_signal_clusters.py

from django.core.management.base import BaseCommand
from intel.models import Signal, SignalCluster
from intel.services.clustering import (
    assign_signal_to_cluster,
    rebuild_cluster_aggregate,
)


class Command(BaseCommand):
    help = "Build signal clusters from raw, validated, and verified signals"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Clear existing cluster assignment and rebuild clusters",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of processed signals",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            Signal.objects.update(cluster=None)
            SignalCluster.objects.all().delete()
            self.stdout.write("Existing clusters cleared.")

        qs = (
            Signal.objects
            .exclude(status="noise")
            .filter(status__in=["raw", "validated", "verified"])
            .order_by("-published_at", "-id")
        )

        if options["limit"]:
            qs = qs[: options["limit"]]

        total = qs.count() if hasattr(qs, "count") else len(qs)
        processed = 0
        affected_cluster_ids = set()

        for signal in qs.iterator() if hasattr(qs, "iterator") else qs:
            cluster = assign_signal_to_cluster(signal)
            affected_cluster_ids.add(cluster.id)
            processed += 1

            if processed % 100 == 0:
                self.stdout.write(f"Processed {processed}/{total}")

        clusters = SignalCluster.objects.filter(id__in=affected_cluster_ids)

        for cluster in clusters:
            rebuild_cluster_aggregate(cluster)

        self.stdout.write(
            self.style.SUCCESS(
                f"Cluster build completed: {processed} signals, "
                f"{clusters.count()} clusters updated"
            )
        )