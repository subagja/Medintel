from django import forms

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