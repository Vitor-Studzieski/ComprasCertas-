"""Tenant-scoped suppliers and immutable product purchasing conditions."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.identity.models import Organization


class TenantRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_records",
    )

    class Meta:
        abstract = True


class Supplier(TenantRecord):
    name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=160, blank=True, default="")
    email = models.EmailField(max_length=254, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="catalog_suppliers_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="catalog_suppliers_updated",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_supplier"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "organization",
                name="catalog_supplier_name_ci_unique",
            ),
        ]


class SupplierProductCondition(TenantRecord):
    """Immutable, effective-dated buying terms shared by a network or store."""

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="product_conditions",
    )
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.PROTECT,
        related_name="supplier_conditions",
    )
    store = models.ForeignKey(
        "inventory.Store",
        on_delete=models.PROTECT,
        related_name="supplier_product_conditions",
        null=True,
        blank=True,
    )
    effective_from = models.DateField()
    purchase_unit_name = models.CharField(max_length=40)
    base_units_per_purchase_unit = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        help_text="Unidades-base de estoque contidas em uma unidade de compra.",
    )
    price_per_purchase_unit = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Preço em reais por unidade de compra.",
    )
    delivery_lead_time_days = models.PositiveSmallIntegerField(
        help_text="Prazo de entrega contado em dias corridos."
    )
    minimum_purchase_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        help_text="Pedido mínimo expresso na unidade de compra.",
    )
    purchase_multiple = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        help_text="Múltiplo de compra expresso na unidade de compra.",
    )
    is_available = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="catalog_supplier_product_conditions_created",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "catalog_supplier_product_condition"
        ordering = ["product__sku", "supplier__name", "store_id", "effective_from", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "supplier", "product", "effective_from"],
                condition=Q(store__isnull=True),
                name="catalog_condition_network_version_unique",
            ),
            models.UniqueConstraint(
                fields=["organization", "store", "supplier", "product", "effective_from"],
                condition=Q(store__isnull=False),
                name="catalog_condition_store_version_unique",
            ),
            models.CheckConstraint(
                condition=Q(base_units_per_purchase_unit__gt=0),
                name="catalog_condition_conversion_positive",
            ),
            models.CheckConstraint(
                condition=Q(price_per_purchase_unit__gte=0),
                name="catalog_condition_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(minimum_purchase_quantity__gte=0),
                name="catalog_condition_min_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(purchase_multiple__gt=0),
                name="catalog_condition_multiple_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "product", "effective_from"],
                name="catalog_condition_product_idx",
            ),
            models.Index(
                fields=["organization", "store", "effective_from"],
                name="catalog_condition_store_idx",
            ),
        ]
