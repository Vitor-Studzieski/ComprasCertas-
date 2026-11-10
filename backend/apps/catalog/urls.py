"""Versioned supplier catalog endpoints."""

from django.urls import path

from apps.catalog.views import SupplierDetailView, SupplierListCreateView, SupplierProductConditionsView


urlpatterns = [
    path("suppliers/", SupplierListCreateView.as_view(), name="supplier-list-create"),
    path("suppliers/<uuid:supplier_id>/", SupplierDetailView.as_view(), name="supplier-detail"),
    path(
        "suppliers/<uuid:supplier_id>/product-conditions/",
        SupplierProductConditionsView.as_view(),
        name="supplier-product-conditions",
    ),
]
