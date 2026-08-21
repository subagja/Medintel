from django import forms

from .models import Signal


class SignalFormationForm(forms.Form):
    title = forms.CharField(
        label="Judul sinyal",
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Judul ringkas dan faktual",
            }
        ),
    )
    summary = forms.CharField(
        label="Ringkasan sinyal",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": (
                    "Ringkas penyakit, lokasi, jumlah, waktu, dan sumber."
                ),
            }
        ),
    )
    formation_notes = forms.CharField(
        label="Dasar pembentukan",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Jelaskan alasan artikel layak dibentuk menjadi "
                    "kandidat sinyal."
                ),
            }
        ),
    )

    def __init__(self, *args, article=None, candidate=None, **kwargs):
        super().__init__(*args, **kwargs)

        if article is None:
            return

        if candidate is not None:
            default_title = candidate.suggested_title
            default_summary = candidate.suggested_summary
            default_notes = candidate.suggested_notes
        else:
            default_title = article.title
            default_summary = article.excerpt or article.content_text[:700]
            default_notes = "Artikel telah melalui validasi analis."

        self.initial.setdefault("title", default_title)
        self.initial.setdefault("summary", default_summary)
        self.initial.setdefault("formation_notes", default_notes)


class SignalReviewForm(forms.ModelForm):
    review_notes = forms.CharField(
        label="Catatan keputusan",
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Tuliskan dasar konfirmasi, koreksi, atau penolakan."
                ),
            }
        ),
    )

    class Meta:
        model = Signal
        fields = [
            "title",
            "summary",
            "event_start_date",
            "event_end_date",
            "priority_level",
            "confidence_level",
            "event_classification",
            "classification_basis",
            "analyst_judgement",
            "implication",
            "recommended_action",
            "information_gaps",
        ]
        labels = {
            "title": "Judul sinyal",
            "summary": "Ringkasan sinyal",
            "event_start_date": "Tanggal mulai kejadian",
            "event_end_date": "Tanggal akhir kejadian",
            "priority_level": "Prioritas",
            "confidence_level": "Tingkat keyakinan",
            "event_classification": "Klasifikasi kejadian",
            "classification_basis": "Dasar klasifikasi kejadian",
            "analyst_judgement": "Judgement analis",
            "implication": "Implikasi awal",
            "recommended_action": "Rekomendasi tindak lanjut",
            "information_gaps": "Kesenjangan informasi",
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(
                attrs={"class": "form-control", "rows": 5}
            ),
            "event_start_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "event_end_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "priority_level": forms.Select(attrs={"class": "form-select"}),
            "confidence_level": forms.Select(attrs={"class": "form-select"}),
            "event_classification": forms.Select(
                attrs={"class": "form-select"}
            ),
            "classification_basis": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": (
                        "Contoh: kasus muncul di wilayah non-endemik, "
                        "meningkat kembali setelah lama terkendali, atau "
                        "etiologinya belum diketahui."
                    ),
                }
            ),
            "analyst_judgement": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "implication": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "recommended_action": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            "information_gaps": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("event_start_date")
        end_date = cleaned_data.get("event_end_date")

        if start_date and end_date and end_date < start_date:
            self.add_error(
                "event_end_date",
                "Tanggal akhir tidak boleh mendahului tanggal mulai.",
            )

        classification = cleaned_data.get("event_classification")
        basis = (cleaned_data.get("classification_basis") or "").strip()
        if classification in {
            Signal.EventClassification.EMERGING,
            Signal.EventClassification.RE_EMERGING,
            Signal.EventClassification.UNKNOWN_CLUSTER,
        } and not basis:
            self.add_error(
                "classification_basis",
                "Dasar klasifikasi wajib diisi untuk kejadian non-rutin.",
            )

        return cleaned_data
