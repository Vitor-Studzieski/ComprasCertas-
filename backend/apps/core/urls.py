from django.urls import include, path

from apps.core.views import HealthView


urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("", include("apps.identity.urls")),
    path("", include("apps.inventory.urls")),
]
