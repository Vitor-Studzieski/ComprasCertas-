from django.urls import path

from apps.purchasing.views import (
    PurchaseBudgetSettingsView,
    PurchaseOrderApproveView,
    PurchaseOrderCancelRemainingView,
    PurchaseOrderDetailView,
    PurchaseOrderReceiptListCreateView,
    PurchaseOrderReviewView,
    StorePurchaseOrderListCreateView,
)


urlpatterns = [
    path("purchase-budgets/", PurchaseBudgetSettingsView.as_view(), name="network-purchase-budget"),
    path(
        "stores/<uuid:store_id>/purchase-budget/",
        PurchaseBudgetSettingsView.as_view(),
        name="store-purchase-budget",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/",
        StorePurchaseOrderListCreateView.as_view(),
        name="store-purchase-order-list-create",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/<uuid:order_id>/",
        PurchaseOrderDetailView.as_view(),
        name="store-purchase-order-detail",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/<uuid:order_id>/review/",
        PurchaseOrderReviewView.as_view(),
        name="store-purchase-order-review",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/<uuid:order_id>/approve/",
        PurchaseOrderApproveView.as_view(),
        name="store-purchase-order-approve",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/<uuid:order_id>/receipts/",
        PurchaseOrderReceiptListCreateView.as_view(),
        name="store-purchase-order-receipts",
    ),
    path(
        "stores/<uuid:store_id>/purchase-orders/<uuid:order_id>/cancel-remaining/",
        PurchaseOrderCancelRemainingView.as_view(),
        name="store-purchase-order-cancel-remaining",
    ),
]
