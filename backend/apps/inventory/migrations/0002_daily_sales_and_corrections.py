"""Add daily sales totals, correction audit, and stock-ledger references."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


SALES_TABLES = (
    "inventory_daily_product_sale",
    "inventory_sales_correction",
    "inventory_operation",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_sales_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in SALES_TABLES:
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

        predicate = _tenant_predicate("inventory_daily_product_sale")
        cursor.execute(
            'CREATE POLICY "inventory_daily_product_sale_update_active_member" '
            'ON "inventory_daily_product_sale" '
            f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
        )


def disable_sales_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            'DROP POLICY IF EXISTS "inventory_daily_product_sale_update_active_member" '
            'ON "inventory_daily_product_sale"'
        )
        for table in reversed(SALES_TABLES):
            for action in ("select", "insert"):
                cursor.execute(
                    f'DROP POLICY IF EXISTS "{table}_{action}_active_member" ON "{table}"'
                )
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0005_preauthchallenge_recoverycode_webauthncredential"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyProductSale",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operational_date", models.DateField()),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("spreadsheet", "Planilha")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("operation_id", models.UUIDField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_daily_sales_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_dailyproductsale_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="daily_sales",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="daily_sales",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_daily_product_sale",
                "ordering": ["-operational_date", "product__sku", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store", "product", "operational_date"),
                        name="inv_sale_daily_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "operation_id"),
                        name="inv_sale_op_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("quantity__gte", 0)),
                        name="inv_sale_qty_nonnegative",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["organization", "store", "operational_date"],
                        name="inv_sale_store_date_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalesCorrection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("previous_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("new_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("reason", models.CharField(max_length=500)),
                ("operation_id", models.UUIDField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_sales_corrections_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_salescorrection_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="corrections",
                        to="inventory.dailyproductsale",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_sales_correction",
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "operation_id"),
                        name="inv_sale_correction_op_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("previous_quantity__gte", 0), ("new_quantity__gte", 0)),
                        name="inv_sale_correction_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(("previous_quantity", models.F("new_quantity"))),
                        name="inv_sale_correction_changed",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="InventoryOperation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operation_id", models.UUIDField()),
                (
                    "operation_type",
                    models.CharField(
                        choices=[
                            ("daily_sale", "Venda diária"),
                            ("sales_correction", "Correção de venda"),
                        ],
                        max_length=24,
                    ),
                ),
                ("payload_hash", models.CharField(max_length=64)),
                ("resource_id", models.UUIDField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_operations_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_inventoryoperation_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_operation",
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "operation_id"),
                        name="inv_operation_org_id_unique",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="daily_sale",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movement",
                to="inventory.dailyproductsale",
            ),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="sale_correction",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movement",
                to="inventory.salescorrection",
            ),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="kind",
            field=models.CharField(
                choices=[
                    ("opening", "Abertura de estoque"),
                    ("sale", "Venda diária"),
                    ("sale_correction", "Correção de venda"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("daily_sale__isnull", True),
                        ("kind", "opening"),
                        ("opening_line__isnull", False),
                        ("sale_correction__isnull", True),
                    )
                    | models.Q(
                        ("daily_sale__isnull", False),
                        ("kind", "sale"),
                        ("opening_line__isnull", True),
                        ("quantity_delta__lte", 0),
                        ("sale_correction__isnull", True),
                    )
                    | (
                        models.Q(
                            ("daily_sale__isnull", True),
                            ("kind", "sale_correction"),
                            ("opening_line__isnull", True),
                            ("sale_correction__isnull", False),
                        )
                        & ~models.Q(("quantity_delta", 0))
                    )
                ),
                name="inv_movement_reference_valid",
            ),
        ),
        migrations.RunPython(enable_sales_rls, disable_sales_rls),
    ]
