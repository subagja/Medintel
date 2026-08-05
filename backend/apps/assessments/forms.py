from django import forms
from django.db.models import Case, IntegerField, Value, When

from apps.entities.models import (
    ArticleDisease,
    ArticleLocation,
    Disease,
    Location,
)

from .models import ArticleValidationAssessment


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
                country_code="ID",
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

        return f"{prefix}{location}"


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
