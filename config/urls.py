from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "MergeCo Franchise Planner"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("planner.urls")),
]
