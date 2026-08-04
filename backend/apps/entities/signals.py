from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .geolocation import clear_geolocation_cache
from .models import Location, LocationAlias


@receiver(
    [post_save, post_delete],
    sender=Location,
)
@receiver(
    [post_save, post_delete],
    sender=LocationAlias,
)
def clear_location_indexes(**kwargs) -> None:
    clear_geolocation_cache()

    try:
        from .eligibility import clear_eligibility_caches
    except ImportError:
        return

    clear_eligibility_caches()
