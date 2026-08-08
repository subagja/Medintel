import os

from django.apps import AppConfig


class CollectionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.collection"
    verbose_name = "Collection Management"

    def ready(self) -> None:
        # RUN_MAIN cuma "true" di proses server yang sesungguhnya
        # (bukan proses reloader induk) saat pakai `runserver` -- ini
        # mencegah cleanup jalan dua kali tiap auto-reload, dan
        # mencegahnya jalan sama sekali untuk perintah CLI lain
        # (migrate, test, dst) yang tidak butuh ini.
        if os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from django.core.management import call_command

            call_command("cleanup_stale_jobs", minutes=30)
        except Exception:
            # Jangan sampai kegagalan cleanup mencegah server menyala
            # sama sekali -- ini cuma housekeeping, bukan hal kritis.
            import logging

            logging.getLogger(__name__).exception(
                "Gagal menjalankan cleanup_stale_jobs otomatis saat startup."
            )