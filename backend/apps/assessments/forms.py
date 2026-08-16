from django import forms
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from apps.entities.models import (
    ArticleDisease,
    ArticleLocation,
    Disease,
)
from apps.locations.models import Location

from .models import (
    ArticleValidationAssessment,
    IntelligenceRecommendation,
)


class ArticleValidationAssessmentForm(forms.ModelForm):
    class Meta:
        model = ArticleValidationAssessment
        fields = [
            "validation_status",
            "source_reliability",
            "information_credibility",
            "relevance_notes",
            "assessment_notes",
        ]
        widgets = {
            "validation_status": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "source_reliability": forms.Select(
                attrs={
                    "class": "form-select",
                    "id": "id_source_reliability",
                }
            ),
            "information_credibility": forms.Select(
                attrs={
                    "class": "form-select",
                    "id": "id_information_credibility",
                }
            ),
            "relevance_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": (
                        "Tuliskan hasil pemeriksaan relevansi artikel."
                    ),
                }
            ),
            "assessment_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": (
                        "Tuliskan dasar pemberian nilai A–F dan 1–6."
                    ),
                }
            ),
        }
        labels = {
            "validation_status": "Status validasi",
            "source_reliability": "Reliabilitas sumber",
            "information_credibility": "Kredibilitas informasi",
            "relevance_notes": "Catatan validasi",
            "assessment_notes": "Catatan analis",
        }

    def clean(self):
        cleaned_data = super().clean()

        validation_status = cleaned_data.get("validation_status")
        relevance_notes = cleaned_data.get("relevance_notes", "").strip()
        assessment_notes = cleaned_data.get("assessment_notes", "").strip()
        source_reliability = cleaned_data.get("source_reliability")
        information_credibility = cleaned_data.get(
            "information_credibility"
        )

        if (
            validation_status
            == ArticleValidationAssessment.ValidationStatus.REJECTED
            and not relevance_notes
        ):
            self.add_error(
                "relevance_notes",
                "Alasan penolakan wajib diisi.",
            )

        requires_assessment_notes = (
            source_reliability
            in {
                ArticleValidationAssessment.SourceReliability.D,
                ArticleValidationAssessment.SourceReliability.E,
            }
            or information_credibility in {4, 5}
        )

        if requires_assessment_notes and not assessment_notes:
            self.add_error(
                "assessment_notes",
                (
                    "Catatan analis wajib diisi untuk nilai "
                    "reliabilitas D/E atau kredibilitas 4/5."
                ),
            )

        return cleaned_data


class ArticleValidationStatusForm(forms.ModelForm):
    """Form khusus tab Validasi agar field tab lain tidak menghambat submit."""

    class Meta:
        model = ArticleValidationAssessment
        fields = [
            "validation_status",
            "relevance_notes",
        ]
        widgets = {
            "validation_status": forms.Select(
                attrs={"class": "form-select"}
            ),
            "relevance_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": (
                        "Tuliskan hasil pemeriksaan relevansi artikel."
                    ),
                }
            ),
        }
        labels = {
            "validation_status": "Status validasi",
            "relevance_notes": "Catatan validasi",
        }

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("validation_status")
            == ArticleValidationAssessment.ValidationStatus.REJECTED
            and not cleaned_data.get("relevance_notes", "").strip()
        ):
            self.add_error(
                "relevance_notes",
                "Alasan penolakan wajib diisi.",
            )
        return cleaned_data


class NewCountryLocationForm(forms.Form):
    country_name = forms.CharField(
        label="Nama negara",
        max_length=200,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Contoh: Israel",
                "autocomplete": "country-name",
            }
        ),
    )
    country_code = forms.CharField(
        label="Kode negara ISO (2 huruf)",
        min_length=2,
        max_length=2,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control text-uppercase",
                "placeholder": "Contoh: IL",
                "autocomplete": "off",
                "inputmode": "text",
                "maxlength": "2",
            }
        ),
    )
    country_notes = forms.CharField(
        label="Dasar penetapan lokasi",
        required=True,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": (
                    "Contoh: Artikel menyebut kejadian dan angka kasus "
                    "secara tegas terjadi di Israel."
                ),
            }
        ),
        error_messages={
            "required": "Dasar penetapan lokasi wajib diisi.",
        },
    )

    def clean_country_code(self):
        code = self.cleaned_data["country_code"].strip().upper()
        if not code.isalpha() or len(code) != 2:
            raise forms.ValidationError(
                "Gunakan kode negara ISO yang terdiri dari 2 huruf."
            )
        return code


class PrimaryArticleDiseaseForm(forms.Form):
    primary_disease = forms.ModelChoiceField(
        queryset=Disease.objects.none(),
        label="Penyakit utama",
        empty_label="Pilih penyakit utama",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "id_primary_disease",
            }
        ),
        help_text=(
            "Penyakit yang terdeteksi pada artikel ditampilkan paling "
            "atas. Penyakit lain tetap disimpan sebagai konteks."
        ),
    )

    disease_correction_notes = forms.CharField(
        label="Dasar penetapan penyakit",
        required=True,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Contoh: Judul, isi artikel, dan fakta 5.101 "
                    "kasus secara tegas merujuk Tuberkulosis."
                ),
            }
        ),
        error_messages={
            "required": "Dasar penetapan penyakit wajib diisi.",
        },
    )

    def __init__(self, *args, article, **kwargs):
        super().__init__(*args, **kwargs)

        candidate_ids = list(
            ArticleDisease.objects.filter(
                article=article,
            ).values_list(
                "disease_id",
                flat=True,
            )
        )

        self.candidate_ids = set(candidate_ids)

        queryset = (
            Disease.objects.filter(is_active=True)
            .annotate(
                article_candidate_order=Case(
                    When(
                        pk__in=candidate_ids,
                        then=Value(0),
                    ),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
            )
            .order_by(
                "article_candidate_order",
                "name",
            )
        )

        disease_field = self.fields["primary_disease"]
        disease_field.queryset = queryset
        disease_field.label_from_instance = (
            self._disease_label
        )

        current_primary_ids = list(
            ArticleDisease.objects.filter(
                article=article,
                is_primary=True,
            ).values_list(
                "disease_id",
                flat=True,
            )
        )

        if len(current_primary_ids) == 1:
            self.initial.setdefault(
                "primary_disease",
                current_primary_ids[0],
            )
        elif len(candidate_ids) == 1:
            self.initial.setdefault(
                "primary_disease",
                candidate_ids[0],
            )

    def _disease_label(self, disease: Disease) -> str:
        prefix = (
            "Terdeteksi — "
            if disease.pk in self.candidate_ids
            else ""
        )

        return f"{prefix}{disease}"


class PrimaryArticleLocationForm(forms.Form):
    primary_location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        label="Lokasi kejadian utama",
        empty_label="Pilih lokasi utama",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "id": "id_primary_location",
                "data-location-autocomplete-select": "true",
            }
        ),
        help_text=(
            "Lokasi yang terdeteksi pada artikel ditampilkan paling "
            "atas. Lokasi lain tetap disimpan sebagai konteks."
        ),
    )

    correction_notes = forms.CharField(
        label="Dasar koreksi lokasi",
        required=True,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Contoh: Angka kasus pada artikel secara tegas "
                    "merujuk Kabupaten Tangerang."
                ),
            }
        ),
        error_messages={
            "required": "Dasar koreksi lokasi wajib diisi.",
        },
    )

    def __init__(self, *args, article, **kwargs):
        super().__init__(*args, **kwargs)

        candidate_ids = list(
            ArticleLocation.objects.filter(
                article=article,
            ).values_list(
                "location_id",
                flat=True,
            )
        )

        self.candidate_ids = set(candidate_ids)

        queryset = (
            Location.objects.filter(
                is_active=True,
                administrative_level__in=[
                    Location.AdministrativeLevel.COUNTRY,
                    Location.AdministrativeLevel.PROVINCE,
                    Location.AdministrativeLevel.REGENCY,
                    Location.AdministrativeLevel.CITY,
                ],
            )
            .select_related("parent")
            .annotate(
                article_candidate_order=Case(
                    When(
                        pk__in=candidate_ids,
                        then=Value(0),
                    ),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                country_order=Case(
                    When(country_code="ID", then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                administrative_order=Case(
                    When(
                        administrative_level__in=[
                            Location.AdministrativeLevel.REGENCY,
                            Location.AdministrativeLevel.CITY,
                        ],
                        then=Value(0),
                    ),
                    When(
                        administrative_level=(
                            Location.AdministrativeLevel.PROVINCE
                        ),
                        then=Value(1),
                    ),
                    default=Value(2),
                    output_field=IntegerField(),
                ),
            )
            .order_by(
                "article_candidate_order",
                "country_order",
                "country_code",
                "administrative_order",
                "parent__name",
                "name",
            )
        )

        location_field = self.fields["primary_location"]
        location_field.queryset = queryset
        location_field.label_from_instance = (
            self._location_label
        )

        current_primary_ids = list(
            ArticleLocation.objects.filter(
                article=article,
                is_primary=True,
            ).values_list(
                "location_id",
                flat=True,
            )
        )

        if len(current_primary_ids) == 1:
            self.initial.setdefault(
                "primary_location",
                current_primary_ids[0],
            )
        elif len(candidate_ids) == 1:
            self.initial.setdefault(
                "primary_location",
                candidate_ids[0],
            )

    def _location_label(self, location: Location) -> str:
        prefix = (
            "Terdeteksi — "
            if location.pk in self.candidate_ids
            else ""
        )

        country_suffix = (
            ""
            if location.country_code == "ID"
            else f" [{location.country_code}]"
        )
        return f"{prefix}{location}{country_suffix}"


SCORE_1_TO_5_CHOICES = [
    ("", "Pilih skor 1–5"),
    (1, "1 — Sangat rendah"),
    (2, "2 — Rendah"),
    (3, "3 — Sedang"),
    (4, "4 — Tinggi"),
    (5, "5 — Sangat tinggi"),
]

QUALITY_SCORE_CHOICES = [
    ("", "Pilih tingkat kualitas"),
    (0.20, "20% — Sangat rendah"),
    (0.40, "40% — Rendah"),
    (0.60, "60% — Sedang"),
    (0.80, "80% — Tinggi"),
    (1.00, "100% — Sangat tinggi"),
]


class SignalThreatAssessmentForm(forms.Form):
    urgency_score = forms.TypedChoiceField(
        label="Urgensi penanganan",
        choices=SCORE_1_TO_5_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    impact_score = forms.TypedChoiceField(
        label="Potensi dampak",
        choices=SCORE_1_TO_5_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    geographic_scope_score = forms.TypedChoiceField(
        label="Cakupan geografis",
        choices=SCORE_1_TO_5_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    development_speed_score = forms.TypedChoiceField(
        label="Kecepatan perkembangan",
        choices=SCORE_1_TO_5_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    vulnerability_score = forms.TypedChoiceField(
        label="Kerentanan sasaran",
        choices=SCORE_1_TO_5_CHOICES,
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    information_completeness_score = forms.TypedChoiceField(
        label="Kelengkapan informasi",
        choices=QUALITY_SCORE_CHOICES,
        coerce=float,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    evidence_consistency_score = forms.TypedChoiceField(
        label="Konsistensi bukti",
        choices=QUALITY_SCORE_CHOICES,
        coerce=float,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    analytical_judgement = forms.CharField(
        label="Judgement analitis",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Nilai arti sinyal, kecenderungan, dan alasan tingkat "
                    "perhatian yang dipilih."
                ),
            }
        ),
    )
    implications = forms.CharField(
        label="Implikasi",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )
    recommended_actions = forms.CharField(
        label="Rekomendasi tindakan",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )
    assumptions = forms.CharField(
        label="Asumsi",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )
    limitations = forms.CharField(
        label="Keterbatasan dan gap informasi",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )


class ApplyAssessmentRecommendationForm(forms.Form):
    decision_notes = forms.CharField(
        label="Dasar keputusan analis",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": (
                    "Jelaskan alasan menerima rekomendasi prioritas dan "
                    "tingkat keyakinan."
                ),
            }
        ),
    )


class EarlyWarningIssueForm(forms.Form):
    title = forms.CharField(
        label="Judul peringatan",
        max_length=500,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Judul ringkas peringatan dini internal",
            }
        ),
    )
    summary = forms.CharField(
        label="Ringkasan eksekutif",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Ringkas kejadian, penyakit, lokasi, dan bukti utama."
                ),
            }
        ),
    )
    recommended_actions = forms.CharField(
        label="Rekomendasi tindakan",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Tindakan verifikasi, pemantauan, atau koordinasi yang "
                    "perlu dilakukan."
                ),
            }
        ),
    )
    decision_notes = forms.CharField(
        label="Dasar penerbitan analis",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Jelaskan alasan assessment layak diterbitkan sebagai "
                    "peringatan dini internal."
                ),
            }
        ),
    )


class EarlyWarningCloseForm(forms.Form):
    closure_notes = forms.CharField(
        label="Dasar penutupan",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Jelaskan bukti atau perkembangan yang mendasari "
                    "penutupan peringatan."
                ),
            }
        ),
    )


class IntelligenceRecommendationDraftForm(forms.Form):
    title = forms.CharField(
        label="Judul rekomendasi",
        max_length=500,
        strip=True,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    situation_summary = forms.CharField(
        label="Ringkasan situasi",
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 4}
        ),
    )
    objective = forms.CharField(
        label="Tujuan yang ingin dicapai",
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )
    recommended_action = forms.CharField(
        label="Tindakan yang direkomendasikan",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": (
                    "Tuliskan tindakan spesifik, dapat dilaksanakan, dan "
                    "dapat ditelusuri."
                ),
            }
        ),
    )
    action_category = forms.ChoiceField(
        label="Kategori tindakan",
        choices=IntelligenceRecommendation.ActionCategory.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    urgency = forms.ChoiceField(
        label="Urgensi",
        choices=IntelligenceRecommendation.Urgency.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    target_unit = forms.CharField(
        label="Sasaran/unit yang dituju",
        max_length=500,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": (
                    "Contoh: Dinas Kesehatan Kabupaten Tangerang"
                ),
            }
        ),
    )
    due_date = forms.DateField(
        label="Tenggat tindak lanjut",
        required=False,
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}
        ),
    )
    success_indicators = forms.CharField(
        label="Indikator keberhasilan",
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )
    assumptions = forms.CharField(
        label="Asumsi",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )
    information_gaps = forms.CharField(
        label="Gap informasi",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )

    def clean_due_date(self):
        due_date = self.cleaned_data.get("due_date")
        if due_date and due_date < timezone.localdate():
            raise forms.ValidationError(
                "Tenggat rekomendasi tidak boleh berada di masa lalu."
            )
        return due_date


class IntelligenceRecommendationDecisionForm(forms.Form):
    decision_notes = forms.CharField(
        label="Dasar penetapan analis",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Jelaskan mengapa rekomendasi layak ditetapkan sebagai "
                    "dukungan keputusan."
                ),
            }
        ),
    )


class IntelligenceRecommendationProgressForm(forms.Form):
    progress_notes = forms.CharField(
        label="Catatan dimulainya tindak lanjut",
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2}
        ),
    )


class IntelligenceRecommendationCompleteForm(forms.Form):
    completion_notes = forms.CharField(
        label="Hasil tindak lanjut",
        strip=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Catat hasil, bukti, atau respons yang diperoleh."
                ),
            }
        ),
    )


class IntelligenceRecommendationCancelForm(forms.Form):
    cancellation_reason = forms.CharField(
        label="Alasan pembatalan",
        strip=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )
