from django.apps import AppConfig


class LocationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.locations'

    def ready(self) -> None:
        from . import signals  # noqa: F401
