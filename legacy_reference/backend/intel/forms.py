from django import forms
from django.contrib.auth.models import User, Group
from .models import Location, Signal, SignalLocation, LocationAlias, ScoringRule, SystemSetting, Alert, DiseaseMaster

CLEAN_GEOCODE_STATUS_CHOICES = [
    ("pending", "Pending"),
    ("ok", "OK"),
    ("matched", "Matched"),
    ("gazetteer_only", "Gazetteer Only"),
    ("manual", "Manual"),
    ("empty_loc", "Empty Location"),
    ("not_found", "Not Found"),
    ("net_err", "Network Error"),
    ("skip_noise", "Skip Noise"),
    ("skip_too_general", "Skip Too General"),
    ("skip_low_conf", "Skip Low Confidence"),
]


GEOCODE_STATUS_NORMALIZATION = {
    "OK": "ok",
    "EMPTY_LOC": "empty_loc",
    "NOT_FOUND": "not_found",
    "NET_ERR": "net_err",
    "SKIP_NOISE": "skip_noise",
    "SKIP_TOO_GENERAL": "skip_too_general",
    "SKIP_LOW_CONF": "skip_low_conf",
    "MANUAL": "manual",
    "PENDING": "pending",
}


def normalize_geocode_status_value(value):
    value = value or ""
    return GEOCODE_STATUS_NORMALIZATION.get(value, value.lower())

class UserRoleAssignmentForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.all().order_by("username"),
        widget=forms.Select(attrs={"class": "form-control"})
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all().order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=False
    )
    
class GeocodeManualUpdateForm(forms.Form):
    raw_location_text = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control"})
    )
    geocode_status = forms.ChoiceField(
        choices=CLEAN_GEOCODE_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"})
    )
    province = forms.ModelChoiceField(
        queryset=Location.objects.filter(
            level="province",
            is_active=True,
            is_false_positive=False
        ).order_by("display_name", "name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_province"})
    )
    kabkota = forms.ModelChoiceField(
        queryset=Location.objects.filter(
            level__in=["city", "regency"],
            is_active=True,
            is_false_positive=False
        ).order_by("display_name", "name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_kabkota"})
    )
    confidence = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "form-control"})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"})
    )

    def __init__(self, *args, **kwargs):
        province_code = kwargs.pop("province_code", None)
        super().__init__(*args, **kwargs)

        if self.initial.get("geocode_status"):
            self.initial["geocode_status"] = normalize_geocode_status_value(
                self.initial["geocode_status"]
            )

        self.fields["kabkota"].queryset = Location.objects.filter(
            level__in=["city", "regency"],
            is_active=True,
            is_false_positive=False
        ).order_by("display_name", "name")

        if province_code:
            self.fields["kabkota"].queryset = self.fields["kabkota"].queryset.filter(
                province_code=province_code
            )

    def clean_geocode_status(self):
        return normalize_geocode_status_value(
            self.cleaned_data.get("geocode_status")
        )

class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = [
            "name",
            "display_name",
            "level",
            "parent",
            "country_code",
            "province_code",
            "city_regency_code",
            "lat",
            "lon",
            "is_active",
            "is_false_positive",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "display_name": forms.TextInput(attrs={"class": "form-control"}),
            "level": forms.Select(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-control"}),
            "country_code": forms.TextInput(attrs={"class": "form-control"}),
            "province_code": forms.TextInput(attrs={"class": "form-control"}),
            "city_regency_code": forms.TextInput(attrs={"class": "form-control"}),
            "lat": forms.NumberInput(attrs={"step": "any", "class": "form-control"}),
            "lon": forms.NumberInput(attrs={"step": "any", "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["parent"].queryset = Location.objects.filter(
            is_active=True
        ).order_by("display_name", "name")


class LocationAliasForm(forms.ModelForm):
    class Meta:
        model = LocationAlias
        fields = ["location", "alias", "is_primary", "is_active"]
        widgets = {
            "location": forms.Select(attrs={"class": "form-control"}),
            "alias": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(
            is_active=True, is_false_positive=False
        ).order_by("display_name", "name")


class DiseaseMasterForm(forms.ModelForm):
    class Meta:
        model = DiseaseMaster
        fields = [
            "name",
            "aliases",
            "skdr_code",
            "skdr_priority",
            "report_24h",
            "emerging_watchlist",
            "reemerging_watch",
            "disease_type",
            "severity_weight",
            "alert_rule",
            "keyword_id",
            "keyword_en",
            "notes",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "aliases": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "skdr_code": forms.TextInput(attrs={"class": "form-control"}),
            "disease_type": forms.Select(attrs={"class": "form-control"}),
            "severity_weight": forms.Select(attrs={"class": "form-control"}),
            "alert_rule": forms.Select(attrs={"class": "form-control"}),
            "keyword_id": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "keyword_en": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }


class ScoringRuleForm(forms.ModelForm):
    class Meta:
        model = ScoringRule
        fields = ["name", "rule_type", "keyword", "weight", "is_active", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "rule_type": forms.Select(attrs={"class": "form-control"}),
            "keyword": forms.TextInput(attrs={"class": "form-control"}),
            "weight": forms.NumberInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }


class SystemSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = ["key", "value", "description", "is_active"]
        widgets = {
            "key": forms.TextInput(attrs={"class": "form-control"}),
            "value": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

class AlertStatusForm(forms.ModelForm):
    class Meta:
        model = Alert
        fields = ["status", "description"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        }
