from rest_framework import serializers
from .models import Signal, SignalLocation


class SignalSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()

    class Meta:
        model = Signal
        fields = [
            "id",
            "disease_tag",
            "threat_score",
            "status",
            "published_at",
            "crawled_at",
            "title",
            "url",
            "final_url",
            "summary",
            "source_name",
        ]

    def get_source_name(self, obj):
        return obj.source.name if obj.source_id else None


class PointSerializer(serializers.ModelSerializer):
    signal_id = serializers.IntegerField(source="signal.id", read_only=True)
    disease_tag = serializers.CharField(source="signal.disease_tag", read_only=True)
    threat_score = serializers.IntegerField(source="signal.threat_score", read_only=True)
    title = serializers.CharField(source="signal.title", read_only=True)
    source_name = serializers.SerializerMethodField()
    link = serializers.CharField(source="signal.url", read_only=True)
    admin_province = serializers.SerializerMethodField()
    admin_kabkota = serializers.SerializerMethodField()
    location_level = serializers.SerializerMethodField()

    class Meta:
        model = SignalLocation
        fields = [
            "id",
            "signal_id",
            "disease_tag",
            "threat_score",
            "title",
            "source_name",
            "link",
            "raw_location_text",
            "geocode_status",
            "method",
            "confidence",
            "lat",
            "lon",
            "is_primary",
            "created_at",
            "admin_province",
            "admin_kabkota",
            "location_level",
        ]

    def get_source_name(self, obj):
        if obj.signal_id and obj.signal.source_id:
            return obj.signal.source.name
        return None

    def get_location_level(self, obj):
        if obj.location_id:
            return obj.location.level
        return None

    def get_admin_province(self, obj):
        """
        Mengembalikan nama provinsi berdasar hierarchy Location:
        - kalau level province -> dirinya
        - kalau kab/kota/district -> naik ke parent sampai province
        """
        loc = getattr(obj, "location", None)
        if not loc:
            return None

        cur = loc
        # naik sampai province
        for _ in range(6):
            if cur is None:
                break
            if cur.level == "province":
                return cur.name
            cur = cur.parent
        return None

    def get_admin_kabkota(self, obj):
        """
        Mengembalikan nama kab/kota jika tersedia:
        - kalau level city/regency -> dirinya
        - kalau district -> naik ke city/regency
        """
        loc = getattr(obj, "location", None)
        if not loc:
            return None

        cur = loc
        for _ in range(6):
            if cur is None:
                break
            if cur.level in ["city", "regency", "kabupaten", "kota"]:
                return cur.name
            cur = cur.parent
        # kalau langsung province atau unknown
        return None

class SignalSerializer(serializers.ModelSerializer):
    source_name = serializers.SerializerMethodField()

    class Meta:
        model = Signal
        fields = [
            "id",
            "disease_tag",
            "threat_score",
            "status",
            "published_at",
            "crawled_at",
            "title",
            "url",
            "final_url",
            "summary",
            "source_name",
        ]

    def get_source_name(self, obj):
        return obj.source.name if obj.source_id else None


class PointSerializer(serializers.ModelSerializer):
    signal_id = serializers.IntegerField(source="signal.id", read_only=True)
    disease_tag = serializers.CharField(source="signal.disease_tag", read_only=True)
    threat_score = serializers.IntegerField(source="signal.threat_score", read_only=True)
    title = serializers.CharField(source="signal.title", read_only=True)
    source_name = serializers.SerializerMethodField()
    link = serializers.CharField(source="signal.url", read_only=True)

    # NEW: admin fields for thematic mapping
    location_level = serializers.SerializerMethodField()
    admin_province = serializers.SerializerMethodField()
    admin_kabkota = serializers.SerializerMethodField()

    class Meta:
        model = SignalLocation
        fields = [
            "id",
            "signal_id",
            "disease_tag",
            "threat_score",
            "title",
            "source_name",
            "link",
            "raw_location_text",
            "geocode_status",
            "method",
            "confidence",
            "lat",
            "lon",
            "is_primary",
            "created_at",
            # NEW
            "location_level",
            "admin_province",
            "admin_kabkota",
        ]

    def get_source_name(self, obj):
        if obj.signal_id and obj.signal.source_id:
            return obj.signal.source.name
        return None

    def get_location_level(self, obj):
        if obj.location_id:
            return getattr(obj.location, "level", None)
        return None

    def _walk_to_level(self, loc, levels, max_hops=8):
        """Traverse Location.parent chain until level in `levels`."""
        cur = loc
        for _ in range(max_hops):
            if not cur:
                return None
            lvl = getattr(cur, "level", None)
            if lvl in levels:
                return cur
            cur = getattr(cur, "parent", None)
        return None

    def get_admin_province(self, obj):
        loc = getattr(obj, "location", None)
        if not loc:
            return None
        prov = self._walk_to_level(loc, {"province"})
        return getattr(prov, "name", None) if prov else None

    def get_admin_kabkota(self, obj):
        loc = getattr(obj, "location", None)
        if not loc:
            return None
        kk = self._walk_to_level(loc, {"city", "regency", "kabupaten", "kota"})
        return getattr(kk, "name", None) if kk else None