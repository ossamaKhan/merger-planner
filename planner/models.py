from django.db import models


class DataMonth(models.Model):
    """One monthly snapshot of the site file (Sheet1)."""

    month = models.DateField(unique=True, help_text="First day of the month")
    source_file = models.CharField(max_length=255, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    kpi_columns = models.JSONField(default=list, help_text="KPI columns found in this upload, in file order")
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month"]

    def __str__(self):
        return self.month.strftime("%b %Y")

    @property
    def key(self):
        return self.month.strftime("%Y-%m")


class Franchise(models.Model):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200, blank=True)
    operator = models.CharField(max_length=40, blank=True, help_text="Telenor / Ufone / MergeCo")
    color = models.CharField(max_length=9, blank=True)
    is_planned = models.BooleanField(default=False, help_text="Created in a scenario, not present in source data")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"{self.code} – {self.name}" if self.name and self.name != self.code else self.code


class Site(models.Model):
    month = models.ForeignKey(DataMonth, on_delete=models.CASCADE, related_name="sites")
    site_id = models.CharField(max_length=64, db_index=True)
    operator = models.CharField(max_length=40, blank=True)
    big_region = models.CharField(max_length=80, blank=True, db_index=True, help_text="e.g. Central B (level above Region)")
    region = models.CharField(max_length=80, blank=True, db_index=True)
    bu = models.CharField(max_length=80, blank=True)
    city = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=120, blank=True, help_text="District as written in the source file")
    geo_district = models.CharField(max_length=120, blank=True, db_index=True, help_text="District from boundary polygon")
    geo_tehsil = models.CharField(max_length=120, blank=True)
    province = models.CharField(max_length=80, blank=True)
    franchise_code = models.CharField(max_length=64, db_index=True)
    bm = models.CharField(max_length=120, blank=True)
    arm = models.CharField(max_length=120, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    site_type = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=40, blank=True)
    mocan_date = models.DateField(null=True, blank=True)
    franchise_after_mocan = models.CharField(max_length=64, blank=True)
    tp_mocan = models.CharField(max_length=20, blank=True)
    ufone_mocan = models.CharField(max_length=20, blank=True)
    ufone_mocan_fr = models.CharField(max_length=64, blank=True)
    tp_mocan_fr = models.CharField(max_length=64, blank=True)
    kpis = models.JSONField(default=dict)

    class Meta:
        unique_together = [("month", "site_id")]
        indexes = [models.Index(fields=["month", "franchise_code"])]

    def __str__(self):
        return f"{self.site_id} ({self.month})"


class Scenario(models.Model):
    PROJECTION_CHOICES = [
        ("best", "Lift towards best member's per-site productivity"),
        ("avg", "Lift towards area average per-site productivity"),
        ("none", "No uplift (plain sum)"),
    ]
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    coverage_km = models.FloatField(default=1.0, help_text="Radius one site covers, in km")
    fill_within_district = models.BooleanField(
        default=True, help_text="Fill uncovered islands only with franchises from the same district"
    )
    projection_method = models.CharField(max_length=10, choices=PROJECTION_CHOICES, default="best")
    realization_pct = models.FloatField(default=50.0, help_text="% of the productivity gap expected to close")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class FranchiseMapping(models.Model):
    """Old franchise -> new (post-merger) franchise."""

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="mappings")
    source_code = models.CharField(max_length=64)
    target_code = models.CharField(max_length=64)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("scenario", "source_code")]


class SiteOverride(models.Model):
    """Individual site moved to a franchise different from its franchise's mapping."""

    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="overrides")
    site_id = models.CharField(max_length=64)
    target_code = models.CharField(max_length=64)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("scenario", "site_id")]


class ScopeReview(models.Model):
    """Marks an ASM / BM / city / district / region as reviewed in a scenario."""

    LEVELS = [("arm", "ARM/ASM"), ("bm", "BM"), ("tehsil", "City/Tehsil"), ("district", "District"), ("region", "Region")]
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="reviews")
    level = models.CharField(max_length=10, choices=LEVELS)
    value = models.CharField(max_length=200)
    note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("scenario", "level", "value")]


class BoundaryLayer(models.Model):
    """A boundary file loaded from KML/KMZ: PTA districts (official) or a comparison layer (e.g. Zong)."""

    ROLES = [("districts", "PTA districts — official district borders"),
             ("compare", "Comparison layer — e.g. Zong territories or sites")]
    label = models.CharField(max_length=120)
    role = models.CharField(max_length=10, choices=ROLES)
    source_file = models.CharField(max_length=255)
    raw = models.BinaryField(help_text="Original KML/KMZ, kept so the name field can be changed later")
    name_field = models.CharField(max_length=120, blank=True)
    fields = models.JSONField(default=list)
    counts = models.JSONField(default=dict)
    aliases = models.JSONField(default=dict, blank=True, help_text="Your district spelling -> PTA district name")
    group_field = models.CharField(max_length=120, blank=True, help_text="Point layers: field that groups points into territories")
    active = models.BooleanField(default=True)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role", "-uploaded_at"]

    def __str__(self):
        return f"{self.label} ({self.get_role_display()})"


class BoundaryFeature(models.Model):
    layer = models.ForeignKey(BoundaryLayer, on_delete=models.CASCADE, related_name="features")
    name = models.CharField(max_length=200, db_index=True)
    kind = models.CharField(max_length=10)  # polygon | point
    geometry = models.JSONField()
    props = models.JSONField(default=dict)


class AreaName(models.Model):
    """Your own neighbourhood / area names (e.g. Gulgasht, Cantt). A point (pin) or a drawn polygon.
    They win over the built-in place list when sites are tagged with an area."""

    name = models.CharField(max_length=120)
    city = models.CharField(max_length=120, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    geometry = models.JSONField(null=True, blank=True, help_text="Optional polygon (GeoJSON) — sites inside get this name")
    source = models.CharField(max_length=120, blank=True, help_text="added on map / file name")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["city", "name"]

    def __str__(self):
        return f"{self.name} ({self.city})" if self.city else self.name


class UploadChunk(models.Model):
    """Pieces of a large upload. Vercel accepts at most 4.5 MB per request, so the browser sends big files in pieces."""

    token = models.CharField(max_length=40, db_index=True)
    idx = models.PositiveIntegerField()
    data = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("token", "idx")]
