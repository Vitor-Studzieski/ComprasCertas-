"""Create tenant-scoped supplier and purchasing-condition tables."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone


CATALOG_TABLES = (
    "catalog_supplier",
    "catalog_supplier_product_condition",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_catalog_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in CATALOG_TABLES:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            predicate = _tenant_predicate(table)
            cursor.execute(
                f'CREATE POLICY "{table}_select_active_member" ON "{table}" '
                f"FOR SELECT USING ({predicate})"
            )
            cursor.execute(
                f'CREATE POLICY "{table}_insert_active_member" ON "{table}" '
                f"FOR INSERT WITH CHECK ({predicate})"
            )

        table = "catalog_supplier"
        predicate = _tenant_predicate(table)
        cursor.execute(
            f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
            f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
        )


def disable_catalog_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in reversed(CATALOG_TABLES):
            actions = ["select", "insert"]
            if table == "catalog_supplier":
                actions.append("update")
            for action in actions:
                cursor.execute(
                    f'DROP POLICY IF EXISTS "{table}_{action}_active_member" ON "{table}"'
                )
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("identity", "0005_preauthchallenge_recoverycode_webauthncredential"),
        ("inventory", "0002_daily_sales_and_corrections"),
    ]

    operations = [
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("contact_name", models.CharField(blank=True, default="", max_length=160)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("phone", models.CharField(blank=True, default="", max_length=32)),
                ("notes", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_suppliers_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_suppliers_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_supplier_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "catalog_supplier",
                "ordering": ["name", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        "organization",
                        name="catalog_supplier_name_ci_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SupplierProductCondition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("effective_from", models.DateField()),
                ("purchase_unit_name", models.CharField(max_length=40)),
                (
                    "base_units_per_purchase_unit",
                    models.DecimalField(
                        decimal_places=3,
                        help_text="Unidades-base de estoque contidas em uma unidade de compra.",
                        max_digits=14,
                    ),
                ),
                (
                    "price_per_purchase_unit",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Preço em reais por unidade de compra.",
                        max_digits=14,
                    ),
                ),
                (
                    "delivery_lead_time_days",
                    models.PositiveSmallIntegerField(help_text="Prazo de entrega contado em dias corridos."),
                ),
                (
                    "minimum_purchase_quantity",
                    models.DecimalField(
                        decimal_places=3,
                        help_text="Pedido mínimo expresso na unidade de compra.",
                        max_digits=14,
                    ),
                ),
                (
                    "purchase_multiple",
                    models.DecimalField(
                        decimal_places=3,
                        help_text="Múltiplo de compra expresso na unidade de compra.",
                        max_digits=14,
                    ),
                ),
                ("is_available", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_supplier_product_conditions_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="catalog_supplierproductcondition_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_conditions",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_product_conditions",
                        to="inventory.store",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_conditions",
                        to="catalog.supplier",
                    ),
                ),
            ],
            options={
                "db_table": "catalog_supplier_product_condition",
                "ordering": ["product__sku", "supplier__name", "store_id", "effective_from", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "supplier", "product", "effective_from"),
                        condition=models.Q(("store__isnull", True)),
                        name="catalog_condition_network_version_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "store", "supplier", "product", "effective_from"),
                        condition=models.Q(("store__isnull", False)),
                        name="catalog_condition_store_version_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("base_units_per_purchase_unit__gt", 0)),
                        name="catalog_condition_conversion_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("price_per_purchase_unit__gte", 0)),
                        name="catalog_condition_price_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("minimum_purchase_quantity__gte", 0)),
                        name="catalog_condition_min_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("purchase_multiple__gt", 0)),
                        name="catalog_condition_multiple_positive",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["organization", "product", "effective_from"],
                        name="catalog_condition_product_idx",
                    ),
                    models.Index(
                        fields=["organization", "store", "effective_from"],
                        name="catalog_condition_store_idx",
                    ),
                ],
            },
        ),
        migrations.RunPython(enable_catalog_rls, disable_catalog_rls),
    ]
