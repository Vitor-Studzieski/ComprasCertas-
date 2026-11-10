"""Tenant-scoped purchase orders, budget commitments, and receipt audit."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class TenantRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
    )

    class Meta:
        abstract = True


class PurchaseBudget(TenantRecord):
    """Network defaults and optional per-store weekly/monthly overrides."""

    store = models.ForeignKey(
        "inventory.Store",
        on_delete=models.PROTECT,
        related_name="purchase_budgets",
        null=True,
        blank=True,
    )
    weekly_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    monthly_limit = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    week_starts_on = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_budgets_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_budgets_updated",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "purchasing_budget"
        ordering = ["organization_id", "store_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(store__isnull=True),
                name="pur_budget_network_unique",
            ),
            models.UniqueConstraint(
                fields=["organization", "store"],
                condition=Q(store__isnull=False),
                name="pur_budget_store_unique",
            ),
            models.CheckConstraint(
                condition=(Q(weekly_limit__isnull=True) | Q(weekly_limit__gte=0)),
                name="pur_budget_week_nonnegative",
            ),
            models.CheckConstraint(
                condition=(Q(monthly_limit__isnull=True) | Q(monthly_limit__gte=0)),
                name="pur_budget_month_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(store__isnull=True, week_starts_on__gte=0, week_starts_on__lte=6)
                    | Q(store__isnull=False, week_starts_on__isnull=True)
                ),
                name="pur_budget_week_scope_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(store__isnull=True, weekly_limit__isnull=False, monthly_limit__isnull=False)
                    | Q(store__isnull=False)
                ),
                name="pur_budget_network_limits_required",
            ),
        ]


class PurchaseOrder(TenantRecord):
    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        REVIEWED = "reviewed", "Revisado"
        APPROVED = "approved", "Aprovado"
        PARTIALLY_RECEIVED = "partially_received", "Recebido parcialmente"
        RECEIVED = "received", "Recebido"
        CANCELLED = "cancelled", "Cancelado"
        STALE = "stale", "Desatualizado"

    store = models.ForeignKey("inventory.Store", on_delete=models.PROTECT, related_name="purchase_orders")
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    source_snapshot = models.JSONField(default=dict)
    approved_snapshot = models.JSONField(null=True, blank=True)
    estimated_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    committed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    budget_week_start = models.DateField(null=True, blank=True)
    budget_month_start = models.DateField(null=True, blank=True)
    budget_exception_reason = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_updated",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_reviewed",
        null=True,
        blank=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_approved",
        null=True,
        blank=True,
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_orders_cancelled",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "purchasing_order"
        ordering = ["-created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(estimated_total__gte=0), name="pur_order_total_nonnegative"),
            models.CheckConstraint(condition=Q(committed_amount__gte=0), name="pur_order_commit_nonnegative"),
        ]


class PurchaseOrderItem(TenantRecord):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("inventory.Product", on_delete=models.PROTECT, related_name="purchase_order_items")
    supplier_condition = models.ForeignKey(
        "catalog.SupplierProductCondition",
        on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )
    purchase_unit_name = models.CharField(max_length=40)
    base_units_per_purchase_unit = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    minimum_purchase_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    purchase_multiple = models.DecimalField(max_digits=14, decimal_places=3)
    recommended_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    approved_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    source_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "purchasing_order_item"
        ordering = ["product__sku", "id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="pur_order_item_product_unique"),
            models.CheckConstraint(condition=Q(base_units_per_purchase_unit__gt=0), name="pur_item_conversion_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="pur_item_price_nonnegative"),
            models.CheckConstraint(condition=Q(minimum_purchase_quantity__gte=0), name="pur_item_min_nonnegative"),
            models.CheckConstraint(condition=Q(purchase_multiple__gt=0), name="pur_item_multiple_positive"),
            models.CheckConstraint(condition=Q(recommended_quantity__gte=0), name="pur_item_recommended_nonnegative"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="pur_item_quantity_positive"),
            models.CheckConstraint(condition=Q(received_quantity__gte=0), name="pur_item_received_nonnegative"),
            models.CheckConstraint(
                condition=Q(approved_quantity__isnull=True) | Q(approved_quantity__gte=F("received_quantity")),
                name="pur_item_approved_covers_received",
            ),
        ]


class PurchaseReceipt(TenantRecord):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    operational_date = models.DateField()
    supplier_reference = models.CharField(max_length=120, blank=True, default="")
    budget_exception_reason = models.TextField(blank=True, default="")
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_receipts_created",
    )
    received_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "purchasing_receipt"
        ordering = ["-received_at", "id"]


class PurchaseReceiptLine(TenantRecord):
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="lines")
    order_item = models.ForeignKey(PurchaseOrderItem, on_delete=models.PROTECT, related_name="receipt_lines")
    physical_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    accepted_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    pending_excess_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    actual_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    excess_decision = models.CharField(max_length=12, default="pending")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "purchasing_receipt_line"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(physical_quantity__gt=0), name="pur_receipt_physical_positive"),
            models.CheckConstraint(condition=Q(accepted_quantity__gte=0), name="pur_receipt_accepted_nonnegative"),
            models.CheckConstraint(condition=Q(pending_excess_quantity__gte=0), name="pur_receipt_excess_nonnegative"),
            models.CheckConstraint(condition=Q(actual_unit_price__gte=0), name="pur_receipt_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(physical_quantity=F("accepted_quantity") + F("pending_excess_quantity")),
                name="pur_receipt_quantities_reconcile",
            ),
            models.CheckConstraint(
                condition=Q(excess_decision__in=["pending", "accepted", "rejected"]),
                name="pur_receipt_excess_decision_valid",
            ),
        ]


class PurchaseOrderEvent(TenantRecord):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=32)
    reason = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_order_events_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "purchasing_order_event"
        ordering = ["created_at", "id"]
