from django import forms
from django.utils import timezone

from apps.requirements.models import IntelligenceRequirement
from apps.sources.models import Source

from .models import CollectionSchedule, CollectionSession
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
    source = forms.ChoiceField(
        label="Cakupan sumber",
        required=True,
    )
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
            "source": "Cakupan sumber",
            "recurrence": "Frekuensi",
            "run_time": "Waktu pelaksanaan",
            "include_google_news": "Penemuan tambahan Google News",
            "html_deep_scan": "Pemindaian mendalam HTML",
            "article_limit": "Maks. artikel diproses",
            "candidate_limit": "Maks. kandidat diperiksa",
            "max_attempts": "Maks. percobaan",
            "intelligence_requirement": "Kebutuhan intelijen (opsional)",
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
        from apps.crawlers.unified import get_unified_source_readiness

        available_sources = [
            row.source
            for row in get_unified_source_readiness()
            if row.source.is_active
            and row.source.is_verified
            and row.source.crawl_enabled
        ]
        indonesia_count = sum(
            source.source_type != Source.SourceType.INTERNATIONAL_MEDIA
            for source in available_sources
        )
        self.fields["source"].choices = (
            (
                CollectionSession.Scope.ALL_READY,
                f"Semua sumber aktif dan siap ({len(available_sources)})",
            ),
            (
                CollectionSession.Scope.ALL_INDONESIA,
                f"Semua sumber Indonesia aktif ({indonesia_count})",
            ),
            *(
                (
                    f"source:{source.code}",
                    source.name,
                )
                for source in available_sources
            ),
        )
        if self.instance and self.instance.pk:
            self.initial["source"] = (
                f"source:{self.instance.source.code}"
                if self.instance.source_id
                else self.instance.source_scope
            )
        self.fields["intelligence_requirement"].queryset = (
            IntelligenceRequirement.objects.filter(
                status=IntelligenceRequirement.Status.ACTIVE,
                is_active=True,
            ).order_by("-priority", "code")
        )
        self.fields["intelligence_requirement"].required = False
        self.fields["intelligence_requirement"].empty_label = (
            "Semua kebutuhan / koleksi rutin tanpa pengaitan khusus"
        )
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        for name in ("source", "recurrence", "weekday", "intelligence_requirement"):
            self.fields[name].widget.attrs["class"] = "form-select"

    def clean(self):
        cleaned = super().clean()
        source_selection = cleaned.get("source")
        if source_selection in {
            CollectionSession.Scope.ALL_READY,
            CollectionSession.Scope.ALL_INDONESIA,
        }:
            cleaned["source"] = None
            self._source_scope = source_selection
        elif source_selection and source_selection.startswith("source:"):
            source_code = source_selection.split(":", 1)[1]
            try:
                cleaned["source"] = Source.objects.get(
                    code=source_code,
                    is_active=True,
                    is_verified=True,
                    crawl_enabled=True,
                )
            except Source.DoesNotExist:
                self.add_error(
                    "source",
                    "Sumber tidak ditemukan atau tidak lagi aktif.",
                )
            else:
                self._source_scope = CollectionSession.Scope.SINGLE_SOURCE
        else:
            self.add_error("source", "Cakupan sumber wajib dipilih.")
        self.instance.source_scope = getattr(
            self,
            "_source_scope",
            CollectionSession.Scope.SINGLE_SOURCE,
        )
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
        schedule.source_scope = getattr(
            self,
            "_source_scope",
            CollectionSession.Scope.SINGLE_SOURCE,
        )
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
