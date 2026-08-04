from django import forms

from apps.entities.models import Disease, Location

from .models import Indicator, IndicatorType


class IndicatorReviewForm(forms.ModelForm):
    review_notes = forms.CharField(
        required=False,
        label="Catatan review",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 4}
        ),
    )

    class Meta:
        model = Indicator
        fields = [
            "indicator_type",
            "disease",
            "location",
            "event_date",
            "value",
            "unit",
            "direction",
            "summary",
            "confidence_score",
        ]
        widgets = {
            "indicator_type": forms.Select(attrs={"class": "form-select"}),
            "disease": forms.Select(attrs={"class": "form-select"}),
            "location": forms.Select(attrs={"class": "form-select"}),
            "event_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "value": forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "direction": forms.Select(attrs={"class": "form-select"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "confidence_score": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "1"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["indicator_type"].queryset = IndicatorType.objects.filter(is_active=True)
        self.fields["disease"].queryset = Disease.objects.filter(is_active=True).order_by("name")
        self.fields["location"].queryset = Location.objects.filter(is_active=True).order_by(
            "administrative_level", "name"
        )
