from django import forms
from django.utils import timezone

from apps.requirements.models import IntelligenceRequirement
from apps.sources.models import Source

from .models import CollectionSchedule
from .services.scheduling import calculate_next_run_at


WEEKDAY_CHOICES = (
    (0, "Senin"),
    (1, "Selasa"),
    (2, "Rabu"),
    (3, "Kamis"),
    (4, "Jumat"),
    (5, "Sabtu"),
    (6, "Minggu"),
)


class CollectionScheduleForm(forms.ModelForm):
    weekday = forms.TypedChoiceField(
        label="Hari",
        choices=WEEKDAY_CHOICES,
        coerce=int,
        required=False,
    )

    class Meta:
        model = CollectionSchedule
        fields = (
            "name",
            "source",
            "recurrence",
            "run_time",
            "weekday",
            "include_google_news",
            "html_deep_scan",
            "article_limit",
            "candidate_limit",
            "max_attempts",
            "intelligence_requirement",
            "is_active",
        )
        labels = {
            "name": "Nama jadwal",
            "source": "Sumber",
            "recurrence": "Frekuensi",
            "run_time": "Waktu pelaksanaan",
            "include_google_news": "Penemuan tambahan Google News",
            "html_deep_scan": "Pemindaian mendalam HTML",
            "article_limit": "Maks. artikel diproses",
            "candidate_limit": "Maks. kandidat diperiksa",
            "max_attempts": "Maks. percobaan",
            "intelligence_requirement": "Kebutuhan intelijen",
            "is_active": "Aktifkan jadwal",
        }
        widgets = {
            "run_time": forms.TimeInput(attrs={"type": "time"}),
            "article_limit": forms.NumberInput(attrs={"min": 1, "max": 1000}),
            "candidate_limit": forms.NumberInput(attrs={"min": 1, "max": 1000}),
            "max_attempts": forms.NumberInput(attrs={"min": 1, "max": 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = Source.objects.filter(
            is_active=True,
            is_verified=True,
            crawl_enabled=True,
        ).order_by("name")
        self.fields["intelligence_requirement"].queryset = (
            IntelligenceRequirement.objects.filter(
                status=IntelligenceRequirement.Status.ACTIVE,
                is_active=True,
            ).order_by("-priority", "code")
        )
        self.fields["intelligence_requirement"].required = False
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        for name in ("source", "recurrence", "weekday", "intelligence_requirement"):
            self.fields[name].widget.attrs["class"] = "form-select"

    def clean(self):
        cleaned = super().clean()
        recurrence = cleaned.get("recurrence")
        if recurrence in {
            CollectionSchedule.Recurrence.DAILY,
            CollectionSchedule.Recurrence.WEEKLY,
        } and not cleaned.get("run_time"):
            self.add_error("run_time", "Waktu pelaksanaan wajib diisi.")
        if recurrence == CollectionSchedule.Recurrence.WEEKLY and cleaned.get(
            "weekday"
        ) is None:
            self.add_error("weekday", "Hari wajib dipilih untuk jadwal mingguan.")
        for field_name in ("article_limit", "candidate_limit"):
            value = cleaned.get(field_name)
            if value is not None and not 1 <= value <= 1000:
                self.add_error(field_name, "Nilai harus 1–1000.")
        return cleaned

    def save(self, commit=True):
        schedule = super().save(commit=False)
        if schedule.weekday is None:
            schedule.weekday = 0
        schedule.next_run_at = calculate_next_run_at(
            schedule,
            after=timezone.now(),
        )
        if commit:
            schedule.save()
            self.save_m2m()
        return schedule
