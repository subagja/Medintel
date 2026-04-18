from django import forms
from django.contrib.auth.models import User, Group
from .models import Location, Signal, SignalLocation, LocationAlias, ScoringRule, SystemSetting, Alert

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
        choices=Signal.GEOCODE_STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True, is_false_positive=False).order_by("display_name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"})
    )
    confidence = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "form-control"})
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"})
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