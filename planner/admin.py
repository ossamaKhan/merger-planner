from django.contrib import admin

from planner.models import DataMonth, Franchise, FranchiseMapping, Scenario, Site, SiteOverride


@admin.register(DataMonth)
class DataMonthAdmin(admin.ModelAdmin):
    list_display = ("month", "row_count", "source_file", "uploaded_at")


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "operator", "color", "is_planned")
    list_editable = ("name",)
    search_fields = ("code", "name")
    list_filter = ("operator", "is_planned")


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("site_id", "month", "operator", "region", "geo_district", "district", "franchise_code", "status")
    list_filter = ("month", "operator", "region", "geo_district")
    search_fields = ("site_id", "franchise_code")


class MappingInline(admin.TabularInline):
    model = FranchiseMapping
    extra = 0


class OverrideInline(admin.TabularInline):
    model = SiteOverride
    extra = 0


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("name", "coverage_km", "projection_method", "updated_at")
    inlines = [MappingInline, OverrideInline]
