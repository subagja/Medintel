"""Input koreksi MANUAL data pelaporan epidemiologi; tidak mengubah event_date."""
from django import forms
from .models import ArticleFact


class ArticleFactPeriodReviewForm(forms.ModelForm):
    correction_reason = forms.CharField(
        label="Dasar koreksi (wajib)",
        strip=True,
        min_length=8,
        widget=forms.Textarea(attrs={
            "class": "form-control", "rows": 3,
            "placeholder": "Sebutkan lokasi kutipan, tanggal/periode, dan dasar pembenaran angka.",
        }),
    )

    class Meta:
        model = ArticleFact
        fields = (
            "reporting_period_start", "reporting_period_end",
            "reporting_period_precision", "case_count_basis",
            "death_count", "fact_text",
        )
        labels = {
            "reporting_period_start": "Awal periode pelaporan (inklusif)",
            "reporting_period_end": "Akhir periode pelaporan (inklusif)",
            "reporting_period_precision": "Ketelitian waktu sumber",
            "case_count_basis": "Makna jumlah kasus",
            "death_count": "Kematian dalam cakupan laporan",
            "fact_text": "Kutipan bukti dari artikel sumber",
        }
        widgets = {
            "reporting_period_start": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
            "reporting_period_end": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
            "reporting_period_precision": forms.Select(attrs={"class": "form-select"}),
            "case_count_basis": forms.Select(attrs={"class": "form-select"}),
            "death_count": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "fact_text": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }

    def clean(self):
        data = super().clean()
        start = data.get("reporting_period_start")
        end = data.get("reporting_period_end")
        precision = data.get("reporting_period_precision")
        basis = data.get("case_count_basis")
        if bool(start) != bool(end):
            self.add_error("reporting_period_end", "Awal dan akhir harus sama-sama diisi.")
        elif start and end and start > end:
            self.add_error("reporting_period_end", "Akhir tidak boleh lebih awal.")
        if start and self.instance.event_date is not None:
            self.add_error("reporting_period_start",
                "Fakta ini sudah memiliki tanggal kejadian tunggal. Koreksi tanggal lewat alur review fakta terpisah.")
        if start and precision == ArticleFact.ReportingPrecision.UNSPECIFIED:
            self.add_error("reporting_period_precision", "Pilih ketelitian waktu.")
        if start and basis == ArticleFact.CaseCountBasis.UNKNOWN:
            self.add_error("case_count_basis", "Tentukan apakah angkanya kumulatif atau kasus baru.")
        if not start and precision != ArticleFact.ReportingPrecision.UNSPECIFIED:
            self.add_error("reporting_period_precision", "Isi rentang periode lebih dulu.")
        return data
