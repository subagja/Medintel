from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.articles.models import Article
from apps.assessments.models import ArticleValidationAssessment
from apps.collection.models import CollectionSession
from apps.entities.models import Disease
from apps.locations.models import Location

from .models import (
    IntelligenceRequirement,
    RequirementInformationGap,
    RequirementKeyword,
)


User = get_user_model()


class IntelligenceRequirementForm(forms.ModelForm):
    diseases = forms.ModelMultipleChoiceField(
        queryset=Disease.objects.none(),
        required=True,
        label="Penyakit sasaran",
        help_text="Pilih minimal satu penyakit; pilihan pertama menjadi sasaran utama.",
    )
    locations = forms.ModelMultipleChoiceField(
        queryset=Location.objects.none(),
        required=True,
        label="Wilayah sasaran",
        help_text="Pilih minimal satu wilayah; wilayah turunannya ikut dicocokkan.",
    )
    keywords_text = forms.CharField(
        required=False,
        label="Kata kunci tambahan",
        help_text="Satu kata atau frasa per baris.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    class Meta:
        model = IntelligenceRequirement
        fields = (
            "title",
            "question",
            "description",
            "requirement_type",
            "priority",
            "valid_from",
            "valid_until",
            "assigned_to",
        )
        labels = {
            "title": "Judul kebutuhan",
            "question": "Pertanyaan utama",
            "description": "Latar belakang dan tujuan",
            "requirement_type": "Jenis kebutuhan",
            "priority": "Prioritas",
            "valid_from": "Berlaku mulai",
            "valid_until": "Berlaku sampai",
            "assigned_to": "Penanggung jawab",
        }
        widgets = {
            "question": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 4}),
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["diseases"].queryset = Disease.objects.order_by("name")
        self.fields["locations"].queryset = Location.objects.filter(
            is_active=True
        ).order_by("administrative_level", "name")
        self.fields["assigned_to"].queryset = User.objects.filter(
            Q(is_superuser=True)
            | Q(groups__name__in=("Admin", "Analyst", "Reviewer")),
            is_active=True,
        ).distinct().order_by("username")
        self.fields["assigned_to"].required = False

        if self.instance and self.instance.pk:
            self.fields["diseases"].initial = self.instance.diseases.all()
            self.fields["locations"].initial = self.instance.locations.all()
            self.fields["keywords_text"].initial = "\n".join(
                self.instance.keywords.filter(
                    keyword_type=RequirementKeyword.KeywordType.GENERAL,
                    is_active=True,
                ).values_list("keyword", flat=True)
            )

        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.SelectMultiple):
                field.widget.attrs.setdefault("class", "form-select")
                field.widget.attrs.setdefault("size", "6")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "form-select")
            else:
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        valid_from = cleaned.get("valid_from")
        valid_until = cleaned.get("valid_until")
        if valid_from and valid_until and valid_until < valid_from:
            self.add_error(
                "valid_until",
                "Tanggal akhir tidak boleh mendahului tanggal mulai.",
            )
        return cleaned

    def keywords(self) -> list[str]:
        raw = self.cleaned_data.get("keywords_text", "")
        values = []
        seen = set()
        for line in raw.replace(",", "\n").splitlines():
            keyword = " ".join(line.split())
            key = keyword.casefold()
            if keyword and key not in seen:
                values.append(keyword[:200])
                seen.add(key)
        return values[:30]


class RequirementDecisionForm(forms.Form):
    decision_notes = forms.CharField(
        required=True,
        label="Dasar keputusan",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )


class RequirementAnswerForm(forms.Form):
    answer_summary = forms.CharField(
        required=True,
        label="Simpulan jawaban",
        widget=forms.Textarea(attrs={"rows": 5, "class": "form-control"}),
    )
    decision_notes = forms.CharField(
        required=True,
        label="Dasar penetapan",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )


class RequirementCloseForm(forms.Form):
    closure_notes = forms.CharField(
        required=True,
        label="Catatan penutupan",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )


class RequirementInformationGapForm(forms.ModelForm):
    class Meta:
        model = RequirementInformationGap
        fields = ("description", "priority")
        labels = {
            "description": "Informasi yang masih dibutuhkan",
            "priority": "Prioritas",
        }
        widgets = {
            "description": forms.Textarea(
                attrs={"rows": 3, "class": "form-control"}
            ),
            "priority": forms.Select(attrs={"class": "form-select"}),
        }


class RequirementGapResolutionForm(forms.Form):
    resolution_notes = forms.CharField(
        required=True,
        label="Bukti pemenuhan",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )


class RequirementCollectionLinkForm(forms.Form):
    session = forms.ModelChoiceField(
        queryset=CollectionSession.objects.none(),
        label="Sesi koleksi",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    notes = forms.CharField(
        required=False,
        label="Catatan keterkaitan",
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )

    def __init__(self, *args, requirement=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = CollectionSession.objects.order_by("-created_at")
        if requirement and requirement.pk:
            queryset = CollectionSession.objects.exclude(
                requirement_links__requirement=requirement
            ).order_by("-created_at")
        self.fields["session"].queryset = queryset


class RequirementArticleLinkForm(forms.Form):
    article = forms.ModelChoiceField(
        queryset=Article.objects.none(),
        label="Artikel tervalidasi",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    relevance_reason = forms.CharField(
        required=True,
        label="Alasan relevansi",
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )

    def __init__(self, *args, requirement=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Article.objects.filter(
            validation_assessment__validation_status=(
                ArticleValidationAssessment.ValidationStatus.VALIDATED
            )
        ).select_related("source").order_by("-published_at", "-crawled_at")
        if requirement and requirement.pk:
            queryset = queryset.exclude(
                requirement_links__requirement=requirement
            )
        self.fields["article"].queryset = queryset
