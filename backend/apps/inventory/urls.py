"""Versioned inventory endpoints for setup and opening stock."""

from django.urls import path

from apps.inventory.views import (
    OpeningStockCountCreateView,
    OrganizationOperationalCalendarVersionView,
    OrganizationOperationalCalendarView,
    ProductListCreateView,
    StoreListCreateView,
    StoreOperationalCalendarVersionView,
    StoreProductAssortmentCloseView,
    StoreProductAssortmentListCreateView,
    StoreStockBalanceListView,
)

urlpatterns = [
    path(
        "organizations/current/operational-calendar/",
        OrganizationOperationalCalendarView.as_view(),
        name="organization-operational-calendar",
    ),
    path(
        "organizations/current/operational-calendar/versions/",
        OrganizationOperationalCalendarVersionView.as_view(),
        name="organization-operational-calendar-version-create",
    ),
    path("stores/", StoreListCreateView.as_view(), name="store-list-create"),
    path(
        "stores/<uuid:store_id>/operational-calendar/versions/",
        StoreOperationalCalendarVersionView.as_view(),
        name="store-operational-calendar-version-list-create",
    ),
    path("products/", ProductListCreateView.as_view(), name="product-list-create"),
    path(
        "stores/<uuid:store_id>/product-assortments/",
        StoreProductAssortmentListCreateView.as_view(),
        name="store-product-assortment-list-create",
    ),
    path(
        "stores/<uuid:store_id>/product-assortments/<uuid:assortment_id>/",
        StoreProductAssortmentCloseView.as_view(),
        name="store-product-assortment-close",
    ),
    path(
        "stores/<uuid:store_id>/opening-stock-counts/",
        OpeningStockCountCreateView.as_view(),
        name="store-opening-stock-count-create",
    ),
    path(
        "stores/<uuid:store_id>/stock-balances/",
        StoreStockBalanceListView.as_view(),
        name="store-stock-balance-list",
    ),
]
