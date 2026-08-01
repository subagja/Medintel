import uuid

from django.conf import settings
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Q

from apps.articles.models import Article
from apps.signals.models import Signal
from apps.sources.models import Source


SCORE_0_TO_1_VALIDATORS = [
    MinValueValidator(0.0),
    MaxValueValidator(1.0),
]


SCORE_1_TO_5_VALIDATORS = [
    MinValueValidator(1),
    MaxValueValidator(5),
]


class AssessmentLevel(models.TextChoices):
    LOW = "low", "Rendah"
    MEDIUM = "medium", "Sedang"
    HIGH = "high", "Tinggi"
    VERY_HIGH = "very_high", "Sangat Tinggi"
    UNASSESSED = "unassessed", "Belum Dinilai"


class ArticleValidationAssessment(models.Model):
    """
    Penilaian awal artikel sebelum artikel digunakan untuk membentuk
    atau mendukung suatu sinyal intelijen.

    Penilaian menggunakan Admiralty Code:
    - A–F untuk reliabilitas sumber;
    - 1–6 untuk kredibilitas informasi.
    """

    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Perlu Tinjau"
        VALIDATED = "validated", "Tervalidasi"
        REJECTED = "rejected", "Tidak Relevan"

    class SourceReliability(models.TextChoices):
        A = "A", "Sepenuhnya dapat dipercaya"
        B = "B", "Biasanya dapat dipercaya"
        C = "C", "Cukup dapat dipercaya"
        D = "D", "Biasanya tidak dapat dipercaya"
        E = "E", "Tidak dapat dipercaya"
        F = "F", "Belum dapat dinilai"

    class InformationCredibility(models.IntegerChoices):
        CONFIRMED = 1, "Dikonfirmasi oleh sumber lain"
        PROBABLY_TRUE = 2, "Kemungkinan besar benar"
        POSSIBLY_TRUE = 3, "Mungkin benar"
        DOUBTFUL = 4, "Diragukan"
        IMPROBABLE = 5, "Kemungkinan tidak benar"
        UNASSESSABLE = 6, "Belum dapat dinilai"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    article = models.OneToOneField(
        Article,
        on_delete=models.CASCADE,
        related_name="validation_assessment",
    )

    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.PENDING,
        db_index=True,
    )

    source_reliability = models.CharField(
        max_length=1,
        choices=SourceReliability.choices,
        default=SourceReliability.F,
        db_index=True,
    )

    information_credibility = models.PositiveSmallIntegerField(
        choices=InformationCredibility.choices,
        default=InformationCredibility.UNASSESSABLE,
        db_index=True,
    )

    relevance_notes = models.TextField(
        blank=True,
    )

    assessment_notes = models.TextField(
        blank=True,
    )

    supporting_factors = models.JSONField(
        default=list,
        blank=True,
    )

    limiting_factors = models.JSONField(
        default=list,
        blank=True,
    )

    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="article_validation_assessments",
        null=True,
        blank=True,
    )

    evaluated_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=[
                    "validation_status",
                    "updated_at",
                ],
                name="article_validation_status_idx",
            ),
            models.Index(
                fields=[
                    "source_reliability",
                    "information_credibility",
                ],
                name="article_admiralty_code_idx",
            ),
        ]

    @property
    def admiralty_code(self) -> str:
        return (
            f"{self.source_reliability}"
            f"{self.information_credibility}"
        )

    def __str__(self) -> str:
        return (
            f"{self.article.title} — "
            f"{self.admiralty_code}"
        )


class ArticleValidationHistory(models.Model):
    """
    Audit trail perubahan validasi dan neraca penilaian artikel.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    assessment = models.ForeignKey(
        ArticleValidationAssessment,
        on_delete=models.CASCADE,
        related_name="history",
    )

    previous_status = models.CharField(
        max_length=20,
        choices=ArticleValidationAssessment.ValidationStatus.choices,
        blank=True,
    )

    new_status = models.CharField(
        max_length=20,
        choices=ArticleValidationAssessment.ValidationStatus.choices,
    )

    previous_source_reliability = models.CharField(
        max_length=1,
        choices=ArticleValidationAssessment.SourceReliability.choices,
        blank=True,
    )

    new_source_reliability = models.CharField(
        max_length=1,
        choices=ArticleValidationAssessment.SourceReliability.choices,
    )

    previous_information_credibility = (
        models.PositiveSmallIntegerField(
            choices=(
                ArticleValidationAssessment
                .InformationCredibility
                .choices
            ),
            null=True,
            blank=True,
        )
    )

    new_information_credibility = (
        models.PositiveSmallIntegerField(
            choices=(
                ArticleValidationAssessment
                .InformationCredibility
                .choices
            ),
        )
    )

    change_notes = models.TextField(
        blank=True,
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="article_validation_history",
        null=True,
        blank=True,
    )

    changed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return (
            f"{self.assessment.article.title} — "
            f"{self.new_source_reliability}"
            f"{self.new_information_credibility}"
        )
        

class SourceEvaluation(models.Model):
    """
    Penilaian terhadap reliabilitas sumber dalam konteks sinyal tertentu.

    Nilai ini berbeda dari is_verified pada Source:
    - is_verified = sumber diizinkan masuk sistem;
    - reliability_score = penilaian mutu sumber dalam konteks analisis.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="source_evaluations",
    )

    source = models.ForeignKey(
        Source,
        on_delete=models.PROTECT,
        related_name="intelligence_evaluations",
    )

    historical_accuracy_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Konsistensi ketepatan informasi sumber sebelumnya.",
    )

    authority_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Kewenangan atau kompetensi sumber pada topik terkait.",
    )

    transparency_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Kejelasan asal data, narasumber, dan metode pelaporan.",
    )

    independence_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Derajat independensi sumber dari sumber lain.",
    )

    reliability_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        editable=False,
        db_index=True,
    )

    reliability_level = models.CharField(
        max_length=20,
        choices=AssessmentLevel.choices,
        default=AssessmentLevel.UNASSESSED,
        db_index=True,
    )

    notes = models.TextField(
        blank=True,
    )

    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="source_evaluations",
        null=True,
        blank=True,
    )

    evaluated_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-evaluated_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "signal",
                    "source",
                ],
                name="unique_signal_source_evaluation",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "signal",
                    "reliability_level",
                ],
                name="src_eval_signal_level_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.signal.code} — "
            f"{self.source.name}"
        )


class InformationEvaluation(models.Model):
    """
    Penilaian terhadap isi informasi pada artikel, bukan terhadap
    reputasi sumber penerbitnya.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="information_evaluations",
    )

    article = models.ForeignKey(
        Article,
        on_delete=models.PROTECT,
        related_name="information_evaluations",
    )

    corroboration_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Tingkat dukungan dari sumber atau artikel lain.",
    )

    consistency_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Konsistensi isi dengan bukti lain yang tersedia.",
    )

    specificity_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Kejelasan unsur penyakit, lokasi, waktu, dan angka.",
    )

    timeliness_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Kebaruan informasi terhadap kejadian yang dinilai.",
    )

    credibility_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        editable=False,
        db_index=True,
    )

    credibility_level = models.CharField(
        max_length=20,
        choices=AssessmentLevel.choices,
        default=AssessmentLevel.UNASSESSED,
        db_index=True,
    )

    supports_signal = models.BooleanField(
        default=True,
    )

    contradiction_notes = models.TextField(
        blank=True,
        help_text="Diisi apabila informasi bertentangan dengan bukti lain.",
    )

    notes = models.TextField(
        blank=True,
    )

    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="information_evaluations",
        null=True,
        blank=True,
    )

    evaluated_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-evaluated_at",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "signal",
                    "article",
                ],
                name="unique_signal_article_evaluation",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "signal",
                    "credibility_level",
                ],
                name="info_eval_signal_level_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.signal.code} — "
            f"{self.article.title}"
        )


class SignalAssessment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draf"
        COMPLETED = "completed", "Selesai"
        SUPERSEDED = "superseded", "Digantikan"

    class RecommendedPriority(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"
        CRITICAL = "critical", "Kritis"

    class RecommendedConfidence(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    signal = models.ForeignKey(
        Signal,
        on_delete=models.CASCADE,
        related_name="assessments",
    )

    version = models.PositiveIntegerField(
        default=1,
    )

    is_current = models.BooleanField(
        default=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    urgency_score = models.PositiveSmallIntegerField(
        validators=SCORE_1_TO_5_VALIDATORS,
        help_text="Seberapa cepat sinyal memerlukan perhatian.",
    )

    impact_score = models.PositiveSmallIntegerField(
        validators=SCORE_1_TO_5_VALIDATORS,
        help_text="Besarnya potensi dampak kesehatan atau operasional.",
    )

    geographic_scope_score = models.PositiveSmallIntegerField(
        validators=SCORE_1_TO_5_VALIDATORS,
        help_text="Luas cakupan geografis kejadian.",
    )

    development_speed_score = models.PositiveSmallIntegerField(
        validators=SCORE_1_TO_5_VALIDATORS,
        help_text="Kecepatan perkembangan atau perubahan kejadian.",
    )

    vulnerability_score = models.PositiveSmallIntegerField(
        validators=SCORE_1_TO_5_VALIDATORS,
        help_text="Kerentanan kelompok atau wilayah terdampak.",
    )

    source_reliability_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Agregasi reliabilitas sumber pendukung.",
    )

    information_credibility_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Agregasi kredibilitas informasi pendukung.",
    )

    information_completeness_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Kelengkapan informasi yang tersedia.",
    )

    evidence_consistency_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        help_text="Konsistensi antar-bukti dan antar-sumber.",
    )

    priority_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        editable=False,
        db_index=True,
    )

    confidence_score = models.FloatField(
        validators=SCORE_0_TO_1_VALIDATORS,
        editable=False,
        db_index=True,
    )

    recommended_priority = models.CharField(
        max_length=20,
        choices=RecommendedPriority.choices,
        db_index=True,
    )

    recommended_confidence = models.CharField(
        max_length=20,
        choices=RecommendedConfidence.choices,
        db_index=True,
    )

    analytical_judgement = models.TextField()

    implications = models.TextField(
        blank=True,
    )

    recommended_actions = models.TextField(
        blank=True,
    )

    assumptions = models.TextField(
        blank=True,
    )

    limitations = models.TextField(
        blank=True,
    )

    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="signal_assessments",
        null=True,
        blank=True,
    )

    assessed_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-is_current",
            "-version",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "signal",
                    "version",
                ],
                name="unique_signal_assessment_version",
            ),
            models.UniqueConstraint(
                fields=["signal"],
                condition=Q(is_current=True),
                name="unique_current_signal_assessment",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "signal",
                    "is_current",
                    "status",
                ],
                name="assessment_signal_current_idx",
            ),
            models.Index(
                fields=[
                    "recommended_priority",
                    "recommended_confidence",
                ],
                name="assessment_rec_priority_idx",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.signal.code} — "
            f"Assessment v{self.version}"
        )


class InformationGap(models.Model):
    class GapType(models.TextChoices):
        CASE_DATA = "case_data", "Data Kasus"
        DEATH_DATA = "death_data", "Data Kematian"
        LOCATION = "location", "Lokasi"
        TIME = "time", "Waktu Kejadian"
        DISEASE_CONFIRMATION = (
            "disease_confirmation",
            "Konfirmasi Penyakit",
        )
        OFFICIAL_CONFIRMATION = (
            "official_confirmation",
            "Konfirmasi Resmi",
        )
        SOURCE_CORROBORATION = (
            "source_corroboration",
            "Penguatan Sumber",
        )
        RESPONSE = "response", "Respons"
        OTHER = "other", "Lainnya"

    class Priority(models.TextChoices):
        LOW = "low", "Rendah"
        MEDIUM = "medium", "Sedang"
        HIGH = "high", "Tinggi"

    class Status(models.TextChoices):
        OPEN = "open", "Terbuka"
        RESOLVED = "resolved", "Terpenuhi"
        CANCELLED = "cancelled", "Dibatalkan"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    assessment = models.ForeignKey(
        SignalAssessment,
        on_delete=models.CASCADE,
        related_name="information_gaps",
    )

    gap_type = models.CharField(
        max_length=30,
        choices=GapType.choices,
        db_index=True,
    )

    description = models.TextField()

    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    resolution_notes = models.TextField(
        blank=True,
    )

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_information_gaps",
        null=True,
        blank=True,
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "status",
            "-priority",
            "created_at",
        ]

    def __str__(self) -> str:
        return (
            f"{self.assessment.signal.code} — "
            f"{self.get_gap_type_display()}"
        )