from django.contrib import admin
from django.urls import include, path
from core.views import portal_view

urlpatterns = [
    path("", portal_view, name="portal"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]

