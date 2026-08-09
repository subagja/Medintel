from django import forms
from django.utils import timezone

from .criteria import UAT_TASKS, criteria_for
from .models import EvaluationRecord


class EvaluationRecordForm(forms.ModelForm):
    """Form identitas plus instrumen dinamis sesuai jenis evaluasi."""

    class Meta:
        model = EvaluationRecord
        fields = [
            "evaluation_type",
            "evaluator_name",
            "evaluator_role",
            "institution",
            "evaluation_date",
            "general_findings",
            "recommendations",
        ]
        labels = {
            "evaluation_type": "Jenis evaluasi",
            "evaluator_name": "Nama evaluator/pengguna uji",
            "evaluator_role": "Jabatan atau bidang keahlian",
            "institution": "Instansi",
            "evaluation_date": "Tanggal evaluasi",
            "general_findings": "Temuan umum",
            "recommendations": "Rekomendasi perbaikan",
        }
        widgets = {
            "evaluation_type": forms.Select(attrs={"class": "form-select"}),
            "evaluator_name": forms.TextInput(attrs={"class": "form-control"}),
            "evaluator_role": forms.TextInput(attrs={"class": "form-control"}),
            "institution": forms.TextInput(attrs={"class": "form-control"}),
            "evaluation_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "general_findings": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
            "recommendations": forms.Textarea(
                attrs={"class": "form-control", "rows": 4}
            ),
        }

    def __init__(self, *args, evaluation_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.evaluation_type = (
            evaluation_type
            or self.data.get("evaluation_type")
            or getattr(self.instance, "evaluation_type", None)
            or EvaluationRecord.EvaluationType.EXPERT
        )
        if not self.instance.pk:
            self.initial.setdefault("evaluation_date", timezone.localdate())

        existing_scores = getattr(self.instance, "scores", {}) or {}
        existing_notes = getattr(self.instance, "criterion_notes", {}) or {}
        for criterion in criteria_for(self.evaluation_type):
            code = criterion["code"]
            self.fields[f"score__{code}"] = forms.TypedChoiceField(
                label=criterion["label"],
                choices=[("", "Belum dinilai")] + [
                    (score, str(score)) for score in range(1, 6)
                ],
                coerce=int,
                empty_value=None,
                required=False,
                initial=existing_scores.get(code),
                widget=forms.Select(attrs={"class": "form-select"}),
                help_text=criterion["dimension"],
            )
            self.fields[f"note__{code}"] = forms.CharField(
                label=f"Catatan — {criterion['label']}",
                required=False,
                initial=existing_notes.get(code, ""),
                widget=forms.Textarea(
                    attrs={
                        "class": "form-control",
                        "rows": 2,
                        "placeholder": "Bukti, alasan skor, atau catatan evaluator",
                    }
                ),
            )

        if self.evaluation_type == EvaluationRecord.EvaluationType.UAT:
            existing_tasks = getattr(self.instance, "task_results", {}) or {}
            for code, label in UAT_TASKS:
                self.fields[f"task__{code}"] = forms.ChoiceField(
                    label=label,
                    required=False,
                    initial=existing_tasks.get(code, "not_tested"),
                    choices=[
                        ("not_tested", "Belum diuji"),
                        ("passed", "Berhasil"),
                        ("failed", "Tidak berhasil"),
                    ],
                    widget=forms.Select(attrs={"class": "form-select"}),
                )

    def clean_evaluation_type(self):
        value = self.cleaned_data["evaluation_type"]
        if not self.instance._state.adding and value != self.instance.evaluation_type:
            raise forms.ValidationError(
                "Jenis evaluasi tidak dapat diubah setelah instrumen dibuat."
            )
        return value

    @property
    def criterion_rows(self):
        rows = []
        for criterion in criteria_for(self.evaluation_type):
            code = criterion["code"]
            rows.append(
                {
                    **criterion,
                    "score_field": self[f"score__{code}"],
                    "note_field": self[f"note__{code}"],
                }
            )
        return rows

    @property
    def task_rows(self):
        if self.evaluation_type != EvaluationRecord.EvaluationType.UAT:
            return []
        return [
            {"code": code, "label": label, "field": self[f"task__{code}"]}
            for code, label in UAT_TASKS
        ]

    def instrument_payload(self):
        scores = {}
        notes = {}
        for criterion in criteria_for(self.evaluation_type):
            code = criterion["code"]
            score = self.cleaned_data.get(f"score__{code}")
            if score is not None:
                scores[code] = score
            note = (self.cleaned_data.get(f"note__{code}") or "").strip()
            if note:
                notes[code] = note

        tasks = {}
        if self.evaluation_type == EvaluationRecord.EvaluationType.UAT:
            for code, _label in UAT_TASKS:
                tasks[code] = self.cleaned_data.get(
                    f"task__{code}", "not_tested"
                )
        return scores, notes, tasks


class EvaluationCompletionForm(forms.Form):
    decision_notes = forms.CharField(
        label="Catatan penetapan",
        required=True,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3}
        ),
    )


class SnapshotForm(forms.Form):
    snapshot_title = forms.CharField(
        label="Judul snapshot",
        max_length=300,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
