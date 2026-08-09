from django import forms

from .models import (
    Source,
    SourceSeedUrl,
    SourceUrlPattern,
)


class SourceForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()

        crawl_enabled = cleaned_data.get("crawl_enabled")
        is_active = cleaned_data.get("is_active")
        is_verified = cleaned_data.get("is_verified")

        if crawl_enabled and not is_active:
            self.add_error(
                "crawl_enabled",
                (
                    "Aktifkan sumber terlebih dahulu sebelum "
                    "mengaktifkan crawler."
                ),
            )

        if crawl_enabled and not is_verified:
            self.add_error(
                "crawl_enabled",
                (
                    "Verifikasi sumber terlebih dahulu sebelum "
                    "mengaktifkan crawler."
                ),
            )

        return cleaned_data

    class Meta:
        model = Source
        fields = [
            "name",
            "code",
            "domain",
            "base_url",
            "source_type",
            "is_verified",
            "is_active",
            "verification_notes",
            "crawl_enabled",
            "crawl_strategy",
            "allow_subdomains",
            "max_articles_per_run",
            "request_delay_seconds",
            "request_timeout_seconds",
            "user_agent",
            "crawler_notes",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Contoh: Kementerian Kesehatan",
                }
            ),
            "code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Contoh: kemkes",
                }
            ),
            "domain": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Contoh: kemkes.go.id",
                }
            ),
            "base_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://kemkes.go.id",
                }
            ),
            "source_type": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "verification_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Catatan hasil verifikasi sumber.",
                }
            ),
            "crawl_strategy": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "max_articles_per_run": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                    "max": 1000,
                }
            ),
            "request_delay_seconds": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 0,
                    "step": 0.1,
                }
            ),
            "request_timeout_seconds": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "user_agent": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Kosongkan untuk memakai default.",
                }
            ),
            "crawler_notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Catatan teknis struktur situs.",
                }
            ),
            "is_verified": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "crawl_enabled": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "allow_subdomains": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
        }

        labels = {
            "name": "Nama sumber",
            "code": "Kode sumber",
            "domain": "Domain",
            "base_url": "Base URL",
            "source_type": "Jenis sumber",
            "is_verified": "Sumber telah diverifikasi",
            "is_active": "Sumber aktif",
            "verification_notes": "Catatan verifikasi",
            "crawl_enabled": "Aktifkan crawler",
            "crawl_strategy": "Strategi crawling",
            "allow_subdomains": "Izinkan subdomain",
            "max_articles_per_run": "Maksimal artikel per eksekusi",
            "request_delay_seconds": "Jeda permintaan",
            "request_timeout_seconds": "Timeout permintaan",
            "user_agent": "User-Agent khusus",
            "crawler_notes": "Catatan crawler",
        }


class SourceSeedUrlForm(forms.ModelForm):
    def __init__(
        self,
        *args,
        source: Source | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.source = source

        if source is not None:
            self.instance.source = source

    def clean(self):
        cleaned_data = super().clean()

        source = self.source
        url = cleaned_data.get("url")

        if source is None or not url:
            return cleaned_data

        duplicates = SourceSeedUrl.objects.filter(
            source=source,
            url=url,
        )

        if self.instance.pk:
            duplicates = duplicates.exclude(
                pk=self.instance.pk,
            )

        if duplicates.exists():
            self.add_error(
                "url",
                "URL awal ini sudah terdaftar pada sumber.",
            )

        return cleaned_data

    class Meta:
        model = SourceSeedUrl
        fields = [
            "url",
            "seed_type",
            "priority",
            "is_active",
            "notes",
        ]

        widgets = {
            "url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": (
                        "https://contoh.go.id/berita"
                    ),
                }
            ),
            "seed_type": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "priority": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": (
                        "Catatan mengenai URL awal."
                    ),
                }
            ),
        }

        labels = {
            "url": "URL awal",
            "seed_type": "Jenis URL",
            "priority": "Prioritas",
            "is_active": "URL aktif",
            "notes": "Catatan",
        }

class SourceUrlPatternForm(forms.ModelForm):
    def __init__(
        self,
        *args,
        source: Source | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.source = source

        if source is not None:
            self.instance.source = source

    def clean(self):
        cleaned_data = super().clean()

        source = self.source
        pattern_type = cleaned_data.get(
            "pattern_type"
        )
        match_type = cleaned_data.get(
            "match_type"
        )
        pattern = cleaned_data.get("pattern")

        if not all(
            [
                source,
                pattern_type,
                match_type,
                pattern,
            ]
        ):
            return cleaned_data

        duplicates = SourceUrlPattern.objects.filter(
            source=source,
            pattern_type=pattern_type,
            match_type=match_type,
            pattern=pattern.strip(),
        )

        if self.instance.pk:
            duplicates = duplicates.exclude(
                pk=self.instance.pk,
            )

        if duplicates.exists():
            self.add_error(
                "pattern",
                "Pola URL ini sudah terdaftar pada sumber.",
            )

        return cleaned_data

    class Meta:
        model = SourceUrlPattern
        fields = [
            "pattern_type",
            "match_type",
            "pattern",
            "priority",
            "description",
            "is_active",
        ]

        widgets = {
            "pattern_type": forms.Select(
                attrs={"class": "form-select"}
            ),
            "match_type": forms.Select(
                attrs={"class": "form-select"}
            ),
            "pattern": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "/berita/kesehatan/",
                }
            ),
            "priority": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "description": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Contoh: halaman artikel kesehatan",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
        }

        labels = {
            "pattern_type": "Keputusan",
            "match_type": "Metode pencocokan",
            "pattern": "Pola URL",
            "priority": "Prioritas",
            "description": "Keterangan",
            "is_active": "Pola aktif",
        }
