"""Tenant-scoped, short-lived previews for atomic spreadsheet imports."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.identity.models import Organization


class ImportBatch(models.Model):
    class Kind(models.TextChoices):
        PRODUCTS = "products", "Produtos"
        SUPPLIERS = "suppliers", "Fornecedores"
        CONDITIONS = "conditions", "Condições de compra"
        OPENING_STOCK = "opening_stock", "Estoque inicial"
        DAILY_SALES = "daily_sales", "Vendas diárias"
        OUT_OF_STOCK_DAYS = "out_of_stock_days", "Dias sem estoque"

    class Status(models.TextChoices):
        PREVIEW = "preview", "Prévia"
        CONFIRMED = "confirmed", "Confirmada"
        EXPIRED = "expired", "Expirada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="spreadsheet_import_batches",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="spreadsheet_import_batches",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PREVIEW)
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64)
    rows = models.JSONField(default=list)
    metadata = models.JSONField(default=dict)
    preview = models.JSONField(default=dict)
    target_fingerprint = models.CharField(max_length=64, blank=True, default="")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "imports_batch"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["organization", "created_by", "status"], name="imports_batch_owner_idx"),
            models.Index(fields=["expires_at"], name="imports_batch_expiry_idx"),
        ]
