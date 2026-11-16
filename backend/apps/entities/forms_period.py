"""Input koreksi MANUAL data pelaporan epidemiologi; tidak mengubah event_date."""
from django import forms
from .models import ArticleFact, ArticleLocation
from apps.locations.models import Location


class ArticleFactPeriodReviewForm(forms.ModelForm):
    # Tidak masuk Meta.fields: POST lama tidak pernah menghapus lokasi secara diam-diam.
    corrected_location = forms.ModelChoiceField(
        queryset=Location.objects.none(), required=False,
        label="Koreksi lokasi FAKTA (opsional)",
        empty_label="— Pertahankan lokasi fakta saat ini —",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Pilih hanya bila kutipan sumber menyatakan cakupan angka berbeda. "
            "Pilihan dibatasi pada lokasi artikel dan induk wilayahnya."
        ),
    )
    clear_single_event_date = forms.BooleanField(
        required=False,
        label="Hapus tanggal kejadian tunggal yang tidak didukung sumber",
        help_text=(
            "Centang hanya jika tanggal tersimpan berasal dari tanggal berita/datelin, "
            "bukan tanggal kejadian kasus. Alasan koreksi wajib menjelaskan dasarnya."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        article_id = getattr(self.instance, "article_id", None)
        candidate_ids = set()
        if article_id:
            related = ArticleLocation.objects.filter(article_id=article_id).select_related(
                "location", "location__parent", "location__parent__parent",
            )
            current = getattr(self.instance, "location", None)
            locations = ([current] if current else []) + [entry.location for entry in related]
            for location in locations:
                depth = 0
                while location is not None and depth < 6:
                    candidate_ids.add(location.pk)
                    location = location.parent
                    depth += 1
        self.fields["corrected_location"].queryset = Location.objects.filter(
            pk__in=candidate_ids, is_active=True,
        ).order_by("administrative_level", "name")
        self.order_fields([
            "corrected_location", "clear_single_event_date",
            "reporting_period_start", "reporting_period_end",
            "reporting_period_precision", "case_count_basis",
            "death_count", "fact_text", "correction_reason",
        ])
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
        clear_event_date = data.get("clear_single_event_date", False)
        if bool(start) != bool(end):
            self.add_error("reporting_period_end", "Awal dan akhir harus sama-sama diisi.")
        elif start and end and start > end:
            self.add_error("reporting_period_end", "Akhir tidak boleh lebih awal.")
        # Pada ModelForm, full_clean model akan memvalidasi instance setelah clean().
        # Kosongkan hanya instance memori bila pengguna secara eksplisit mengoreksi
        # tanggal, agar pemeriksaan periode tidak salah menolak input yang benar.
        if clear_event_date:
            if self.instance.event_date is None:
                self.add_error("clear_single_event_date", "Fakta ini tidak memiliki tanggal tunggal yang perlu dihapus.")
            else:
                self.instance.event_date = None
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
