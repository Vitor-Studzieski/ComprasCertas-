"""Inventory catalog, operational calendar, opening count, and stock ledger."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.identity.models import Organization


class TenantRecord(models.Model):
    """Common tenant key for data protected by PostgreSQL row-level security."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
    )

    class Meta:
        abstract = True


class Store(TenantRecord):
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_store"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="inv_store_org_name_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Product(TenantRecord):
    class Unit(models.TextChoices):
        UNIT = "unit", "Unidade"
        KILOGRAM = "kg", "Quilograma"
        LITER = "l", "Litro"

    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=4, choices=Unit.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_product"
        ordering = ["sku", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sku"],
                name="inv_product_org_sku_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} — {self.name}"


class OperationalCalendarDefault(TenantRecord):
    """Append-only network-wide calendar defaults, effective from a date."""

    effective_from = models.DateField()
    time_zone = models.CharField(max_length=64)
    operational_day_start = models.TimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_calendar_defaults_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_operational_calendar_default"
        ordering = ["effective_from", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "effective_from"],
                name="inv_calendar_org_date_unique",
            ),
        ]


class StoreOperationalCalendarOverride(TenantRecord):
    """Append-only per-store overrides; NULL fields inherit network defaults."""

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="calendar_overrides")
    effective_from = models.DateField()
    time_zone = models.CharField(max_length=64, null=True, blank=True)
    operational_day_start = models.TimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_store_calendar_overrides_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_store_calendar_override"
        ordering = ["store_id", "effective_from", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "store", "effective_from"],
                name="inv_calendar_store_date_unique",
            ),
        ]


class StoreProduct(TenantRecord):
    """Product assortment interval for one store: [effective_from, effective_to)."""

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="product_assortments")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="store_assortments")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_assortments_created",
    )
    effective_to_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_assortment_end_dates_set",
        null=True,
        blank=True,
    )
    effective_to_set_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_store_product"
        ordering = ["store_id", "product_id", "effective_from", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "store", "product"],
                name="inv_assortment_product_unique",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_to__gt=F("effective_from")),
                name="inv_assortment_valid_interval",
            ),
        ]


class OpeningStockBatch(TenantRecord):
    """One atomic initial count for a store, with its persisted cutoff date."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        SPREADSHEET = "spreadsheet", "Planilha"

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="opening_stock_batches")
    closed_operational_date = models.DateField()
    cutoff_date = models.DateField()
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_opening_batches_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_opening_stock_batch"
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "store"],
                name="inv_opening_org_store_unique",
            ),
            models.CheckConstraint(
                condition=Q(cutoff_date=F("closed_operational_date") + models.Value(1)),
                name="inv_opening_cutoff_next_date",
            ),
        ]


class OpeningStockLine(TenantRecord):
    batch = models.ForeignKey(OpeningStockBatch, on_delete=models.PROTECT, related_name="lines")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="opening_stock_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="opening_stock_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        db_table = "inventory_opening_stock_line"
        ordering = ["product__sku", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "product"],
                name="inv_opening_line_product_unique",
            ),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="inv_opening_line_qty_nonnegative"),
        ]


class DailyProductSale(TenantRecord):
    """Auditable daily quantity sold for one product in one store."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        SPREADSHEET = "spreadsheet", "Planilha"

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="daily_sales")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="daily_sales")
    operational_date = models.DateField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    operation_id = models.UUIDField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_daily_sales_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_daily_product_sale"
        ordering = ["-operational_date", "product__sku", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "store", "product", "operational_date"],
                name="inv_sale_daily_unique",
            ),
            models.UniqueConstraint(
                fields=["organization", "operation_id"],
                name="inv_sale_op_unique",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=0),
                name="inv_sale_qty_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "store", "operational_date"],
                name="inv_sale_store_date_idx",
            ),
        ]


class SalesCorrection(TenantRecord):
    """Immutable audit record for an explicit change to a daily sales total."""

    sale = models.ForeignKey(
        DailyProductSale,
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    previous_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    new_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=500)
    operation_id = models.UUIDField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_sales_corrections_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_sales_correction"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "operation_id"],
                name="inv_sale_correction_op_unique",
            ),
            models.CheckConstraint(
                condition=Q(previous_quantity__gte=0) & Q(new_quantity__gte=0),
                name="inv_sale_correction_nonnegative",
            ),
            models.CheckConstraint(
                condition=~Q(previous_quantity=F("new_quantity")),
                name="inv_sale_correction_changed",
            ),
        ]


class InventoryOperation(TenantRecord):
    """Organization-wide idempotency ledger for client-generated operation IDs."""

    class Type(models.TextChoices):
        DAILY_SALE = "daily_sale", "Venda diária"
        SALES_CORRECTION = "sales_correction", "Correção de venda"

    operation_id = models.UUIDField()
    operation_type = models.CharField(max_length=24, choices=Type.choices)
    payload_hash = models.CharField(max_length=64)
    resource_id = models.UUIDField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_operations_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_operation"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "operation_id"],
                name="inv_operation_org_id_unique",
            ),
        ]


class StockMovement(TenantRecord):
    """Append-only stock ledger for opening counts, sales, and corrections."""

    class Kind(models.TextChoices):
        OPENING = "opening", "Abertura de estoque"
        SALE = "sale", "Venda diária"
        SALE_CORRECTION = "sale_correction", "Correção de venda"

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="stock_movements")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    operational_date = models.DateField()
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=3)
    opening_line = models.OneToOneField(
        OpeningStockLine,
        on_delete=models.PROTECT,
        related_name="movement",
        null=True,
        blank=True,
    )
    daily_sale = models.OneToOneField(
        DailyProductSale,
        on_delete=models.PROTECT,
        related_name="movement",
        null=True,
        blank=True,
    )
    sale_correction = models.OneToOneField(
        SalesCorrection,
        on_delete=models.PROTECT,
        related_name="movement",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_stock_movements_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "inventory_stock_movement"
        ordering = ["operational_date", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind="opening")
                | (Q(quantity_delta__gte=0) & Q(opening_line__isnull=False)),
                name="inv_opening_movement_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="opening",
                        opening_line__isnull=False,
                        daily_sale__isnull=True,
                        sale_correction__isnull=True,
                    )
                    | Q(
                        kind="sale",
                        opening_line__isnull=True,
                        daily_sale__isnull=False,
                        sale_correction__isnull=True,
                        quantity_delta__lte=0,
                    )
                    | (
                        Q(
                            kind="sale_correction",
                            opening_line__isnull=True,
                            daily_sale__isnull=True,
                            sale_correction__isnull=False,
                        )
                        & ~Q(quantity_delta=0)
                    )
                ),
                name="inv_movement_reference_valid",
            ),
        ]
