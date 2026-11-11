from django.urls import path

from apps.imports.views import ImportPreviewCreateView, ImportPreviewDetailView, ImportTemplateView


urlpatterns = [
    path("imports/templates/<str:kind>/", ImportTemplateView.as_view(), name="import-template"),
    path("imports/previews/", ImportPreviewCreateView.as_view(), name="import-preview-create"),
    path("imports/previews/<uuid:batch_id>/", ImportPreviewDetailView.as_view(), name="import-preview-detail"),
    path("imports/previews/<uuid:batch_id>/confirm/", ImportPreviewDetailView.as_view(), name="import-preview-confirm"),
]
