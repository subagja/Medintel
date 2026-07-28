from django.contrib import admin

from .models import (
    ArticleDisease,
    ArticleFact,
    ArticleLocation,
    Disease,
    DiseaseAlias,
    Location,
    LocationAlias,
)

from apps.entities.services import (
    reject_article_disease,
    reject_article_fact,
    reject_article_location,
    validate_article_disease,
    validate_article_fact,
    validate_article_location,
)

from .models import ExtractionReviewLog


class DiseaseAliasInline(admin.TabularInline):
    model = DiseaseAlias
    extra = 1


class LocationAliasInline(admin.TabularInline):
    model = LocationAlias
    extra = 1


@admin.register(Disease)
class DiseaseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "canonical_name",
        "category",
        "is_priority",
        "is_active",
    )

    list_filter = (
        "category",
        "is_priority",
        "is_active",
    )

    search_fields = (
        "name",
        "canonical_name",
        "code",
        "aliases__alias",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    inlines = [
        DiseaseAliasInline,
    ]


@admin.register(DiseaseAlias)
class DiseaseAliasAdmin(admin.ModelAdmin):
    list_display = (
        "alias",
        "disease",
        "language",
        "is_active",
    )

    list_filter = (
        "language",
        "is_active",
    )

    search_fields = (
        "alias",
        "disease__name",
    )

    list_select_related = (
        "disease",
    )


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    inlines = [
        LocationAliasInline,
    ]

    list_display = (
        "name",
        "administrative_level",
        "parent",
        "code",
        "country_code",
        "is_active",
    )

    list_filter = (
        "administrative_level",
        "country_code",
        "is_active",
    )

    search_fields = (
        "name",
        "code",
        "parent__name",
    )

    list_select_related = (
        "parent",
    )


@admin.register(LocationAlias)
class LocationAliasAdmin(admin.ModelAdmin):
    list_display = (
        "alias",
        "location",
        "language",
        "is_active",
    )

    list_filter = (
        "language",
        "is_active",
        "location__administrative_level",
    )

    search_fields = (
        "alias",
        "location__name",
    )

    list_select_related = (
        "location",
    )


@admin.register(ArticleDisease)
class ArticleDiseaseAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "disease",
        "mention_text",
        "confidence_score",
        "is_primary",
        "validation_status",
    )

    list_filter = (
        "validation_status",
        "extraction_method",
        "is_primary",
        "disease",
    )

    search_fields = (
        "article__title",
        "disease__name",
        "mention_text",
    )

    list_select_related = (
        "article",
        "disease",
        "validated_by",
    )

    actions = [
        "validate_selected_diseases",
        "reject_selected_diseases",
    ]

    @admin.action(
        description="Validasi hasil ekstraksi penyakit terpilih"
    )
    def validate_selected_diseases(
        self,
        request,
        queryset,
    ):
        total = 0

        for relation in queryset:
            validate_article_disease(
                relation=relation,
                reviewer=request.user,
                notes="Validasi melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} hasil ekstraksi penyakit berhasil divalidasi.",
        )


    @admin.action(
        description="Tolak hasil ekstraksi penyakit terpilih"
    )
    def reject_selected_diseases(
        self,
        request,
        queryset,
    ):
        total = 0

        for relation in queryset:
            reject_article_disease(
                relation=relation,
                reviewer=request.user,
                notes="Ditolak melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} hasil ekstraksi penyakit berhasil ditolak.",
        )


@admin.register(ArticleLocation)
class ArticleLocationAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "location",
        "mention_text",
        "confidence_score",
        "is_primary",
        "validation_status",
    )

    list_filter = (
        "validation_status",
        "extraction_method",
        "is_primary",
        "location__administrative_level",
    )

    search_fields = (
        "article__title",
        "location__name",
        "mention_text",
    )

    list_select_related = (
        "article",
        "location",
        "validated_by",
    )

    actions = [
        "validate_selected_locations",
        "reject_selected_locations",
    ]

    @admin.action(
        description="Validasi hasil ekstraksi lokasi terpilih"
    )
    def validate_selected_locations(
        self,
        request,
        queryset,
    ):
        total = 0

        for relation in queryset:
            validate_article_location(
                relation=relation,
                reviewer=request.user,
                notes="Validasi melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} hasil ekstraksi lokasi berhasil divalidasi.",
        )


    @admin.action(
        description="Tolak hasil ekstraksi lokasi terpilih"
    )
    def reject_selected_locations(
        self,
        request,
        queryset,
    ):
        total = 0

        for relation in queryset:
            reject_article_location(
                relation=relation,
                reviewer=request.user,
                notes="Ditolak melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} hasil ekstraksi lokasi berhasil ditolak.",
        )


@admin.register(ArticleFact)
class ArticleFactAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "disease",
        "location",
        "event_date",
        "case_count",
        "death_count",
        "trend",
        "validation_status",
    )

    list_filter = (
        "trend",
        "validation_status",
        "extraction_method",
        "event_date",
    )

    search_fields = (
        "article__title",
        "disease__name",
        "location__name",
        "fact_text",
    )

    list_select_related = (
        "article",
        "disease",
        "location",
        "validated_by",
    )

    actions = [
        "validate_selected_facts",
        "reject_selected_facts",
    ]

    @admin.action(
        description="Validasi fakta artikel terpilih"
    )
    def validate_selected_facts(
        self,
        request,
        queryset,
    ):
        total = 0

        for fact in queryset:
            validate_article_fact(
                fact=fact,
                reviewer=request.user,
                notes="Validasi melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} fakta artikel berhasil divalidasi.",
        )


    @admin.action(
        description="Tolak fakta artikel terpilih"
    )
    def reject_selected_facts(
        self,
        request,
        queryset,
    ):
        total = 0

        for fact in queryset:
            reject_article_fact(
                fact=fact,
                reviewer=request.user,
                notes="Ditolak melalui admin.",
            )
            total += 1

        self.message_user(
            request,
            f"{total} fakta artikel berhasil ditolak.",
        )


@admin.register(ExtractionReviewLog)
class ExtractionReviewLogAdmin(admin.ModelAdmin):
    list_display = (
        "object_type",
        "object_id",
        "action",
        "reviewer",
        "reviewed_at",
    )

    list_filter = (
        "object_type",
        "action",
        "reviewed_at",
    )

    search_fields = (
        "object_id",
        "reviewer__username",
        "notes",
    )

    readonly_fields = (
        "id",
        "object_type",
        "object_id",
        "action",
        "reviewer",
        "before_data",
        "after_data",
        "notes",
        "reviewed_at",
    )

    list_select_related = (
        "reviewer",
    )

    def has_add_permission(
        self,
        request,
    ) -> bool:
        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ) -> bool:
        return False