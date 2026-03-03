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
        ]

    def get_source_name(self, obj):
        if obj.signal_id and obj.signal.source_id:
            return obj.signal.source.name
        return None