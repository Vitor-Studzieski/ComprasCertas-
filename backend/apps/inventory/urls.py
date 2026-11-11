"""Versioned inventory endpoints for setup and opening stock."""

from django.urls import path

from apps.inventory.views import (
    OpeningStockCountCreateView,
    OrganizationOperationalCalendarVersionView,
    OrganizationOperationalCalendarView,
    ProductListCreateView,
    ProductCategoryListCreateView,
    StoreListCreateView,
    StoreOperationalCalendarVersionView,
    StoreProductAssortmentCloseView,
    StoreProductAssortmentListCreateView,
    StoreStockBalanceListView,
    StoreDailyProductSalesView,
    StoreDailySaleCorrectionsView,
)
from apps.inventory.replenishment_views import (
    ReplenishmentPolicyView,
    StoreOutOfStockDaysView,
    StoreReplenishmentRecommendationsView,
    StoreSalesCoverageView,
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
    path("product-categories/", ProductCategoryListCreateView.as_view(), name="product-category-list-create"),
    path("replenishment-settings/", ReplenishmentPolicyView.as_view(), name="network-replenishment-settings"),
    path(
        "product-categories/<uuid:category_id>/replenishment-settings/",
        ReplenishmentPolicyView.as_view(),
        name="category-replenishment-settings",
    ),
    path(
        "products/<uuid:product_id>/replenishment-settings/",
        ReplenishmentPolicyView.as_view(),
        name="product-replenishment-settings",
    ),
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
    path(
        "stores/<uuid:store_id>/replenishment-settings/",
        ReplenishmentPolicyView.as_view(),
        name="store-replenishment-settings",
    ),
    path(
        "stores/<uuid:store_id>/products/<uuid:product_id>/replenishment-settings/",
        ReplenishmentPolicyView.as_view(),
        name="store-product-replenishment-settings",
    ),
    path(
        "stores/<uuid:store_id>/replenishment-recommendations/",
        StoreReplenishmentRecommendationsView.as_view(),
        name="store-replenishment-recommendations",
    ),
    path(
        "stores/<uuid:store_id>/sales-coverage/",
        StoreSalesCoverageView.as_view(),
        name="store-sales-coverage",
    ),
    path(
        "stores/<uuid:store_id>/out-of-stock-days/",
        StoreOutOfStockDaysView.as_view(),
        name="store-out-of-stock-days",
    ),
    path(
        "stores/<uuid:store_id>/daily-sales/",
        StoreDailyProductSalesView.as_view(),
        name="store-daily-sales-list-create",
    ),
    path(
        "stores/<uuid:store_id>/daily-sales/<uuid:daily_sale_id>/corrections/",
        StoreDailySaleCorrectionsView.as_view(),
        name="store-daily-sales-corrections-list-create",
    ),
]
