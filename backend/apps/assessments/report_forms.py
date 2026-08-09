from django import forms
from django.forms import modelformset_factory

from apps.signals.models import Signal

from .models import IntelligenceReport, IntelligenceReportSection
from .services.intelligence_report import eligible_report_signals


SECTION_TEXT_FIELDS = (
    "indikasi_text",
    "analisis_text",
    "dampak_text",
    "upaya_text",
    "saran_tindak_text",
)


class IntelligenceReportCreateForm(forms.Form):
    kepada = forms.CharField(
        label="Kepada",
        max_length=200,
        initial="Yth. Pimpinan",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    dari = forms.CharField(
        label="Dari",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    tembusan = forms.CharField(
        label="Tembusan",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    hal = forms.CharField(
        label="Hal",
        max_length=300,
        required=False,
        help_text="Kosongkan untuk judul otomatis dari sinyal terpilih.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    nilai = forms.CharField(
        label="Nilai informasi",
        max_length=10,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Contoh: B2"}
        ),
    )
    report_date = forms.DateField(
        label="Tanggal laporan",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    signature_block = forms.CharField(
        label="Blok tanda tangan",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    signals = forms.ModelMultipleChoiceField(
        label="Sinyal yang dibahas",
        queryset=Signal.objects.none(),
        widget=forms.CheckboxSelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["signals"].queryset = eligible_report_signals()

    def clean_signals(self):
        signals = self.cleaned_data["signals"]
        if signals.count() > 10:
            raise forms.ValidationError("Satu laporan maksimal memuat 10 sinyal.")
        return signals


class IntelligenceReportHeaderForm(forms.ModelForm):
    class Meta:
        model = IntelligenceReport
        fields = (
            "kepada",
            "dari",
            "tembusan",
            "hal",
            "nilai",
            "signature_block",
        )
        widgets = {
            "kepada": forms.TextInput(attrs={"class": "form-control"}),
            "dari": forms.TextInput(attrs={"class": "form-control"}),
            "tembusan": forms.TextInput(attrs={"class": "form-control"}),
            "hal": forms.TextInput(attrs={"class": "form-control"}),
            "nilai": forms.TextInput(attrs={"class": "form-control"}),
            "signature_block": forms.TextInput(attrs={"class": "form-control"}),
        }


class IntelligenceReportSectionForm(forms.ModelForm):
    class Meta:
        model = IntelligenceReportSection
        fields = SECTION_TEXT_FIELDS
        widgets = {
            field_name: forms.Textarea(
                attrs={"class": "form-control report-section-editor", "rows": 5}
            )
            for field_name in SECTION_TEXT_FIELDS
        }


IntelligenceReportSectionFormSet = modelformset_factory(
    IntelligenceReportSection,
    form=IntelligenceReportSectionForm,
    extra=0,
)


class IntelligenceReportDecisionForm(forms.Form):
    decision_notes = forms.CharField(
        label="Catatan keputusan",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Jelaskan dasar finalisasi, tujuan distribusi, atau alasan arsip."
                ),
            }
        ),
    )
