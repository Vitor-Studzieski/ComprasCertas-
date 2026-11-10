"""Create inventory setup tables and enforce tenant row-level isolation."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLES = (
    "inventory_store",
    "inventory_product",
    "inventory_operational_calendar_default",
    "inventory_store_calendar_override",
    "inventory_store_product",
    "inventory_opening_stock_batch",
    "inventory_opening_stock_line",
    "inventory_stock_movement",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_inventory_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
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

        for table in ("inventory_store", "inventory_store_product"):
            predicate = _tenant_predicate(table)
            cursor.execute(
                f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
                f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
            )


def disable_inventory_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in reversed(TABLES):
            actions = ["select", "insert"]
            if table in ("inventory_store", "inventory_store_product"):
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
    ]

    operations = [
        migrations.CreateModel(
            name="Store",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_store_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_store",
                "ordering": ["name", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "name"),
                        name="inv_store_org_name_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sku", models.CharField(max_length=64)),
                ("name", models.CharField(max_length=200)),
                (
                    "unit",
                    models.CharField(
                        choices=[("unit", "Unidade"), ("kg", "Quilograma"), ("l", "Litro")],
                        max_length=4,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_product_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_product",
                "ordering": ["sku", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "sku"),
                        name="inv_product_org_sku_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="OperationalCalendarDefault",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("effective_from", models.DateField()),
                ("time_zone", models.CharField(max_length=64)),
                ("operational_day_start", models.TimeField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_calendar_defaults_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_operationalcalendardefault_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_operational_calendar_default",
                "ordering": ["effective_from", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "effective_from"),
                        name="inv_calendar_org_date_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StoreOperationalCalendarOverride",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("effective_from", models.DateField()),
                ("time_zone", models.CharField(blank=True, max_length=64, null=True)),
                ("operational_day_start", models.TimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_store_calendar_overrides_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_storeoperationalcalendaroverride_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="calendar_overrides",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_store_calendar_override",
                "ordering": ["store_id", "effective_from", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store", "effective_from"),
                        name="inv_calendar_store_date_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StoreProduct",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("effective_to_set_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_assortments_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "effective_to_set_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_assortment_end_dates_set",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_storeproduct_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="store_assortments",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_assortments",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_store_product",
                "ordering": ["store_id", "product_id", "effective_from", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store", "product"),
                        name="inv_assortment_product_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(effective_to__isnull=True)
                        | models.Q(effective_to__gt=models.F("effective_from")),
                        name="inv_assortment_valid_interval",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="OpeningStockBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("closed_operational_date", models.DateField()),
                ("cutoff_date", models.DateField()),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("spreadsheet", "Planilha")],
                        default="manual",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_opening_batches_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_openingstockbatch_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="opening_stock_batches",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_opening_stock_batch",
                "ordering": ["-created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store"),
                        name="inv_opening_org_store_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(cutoff_date=models.F("closed_operational_date") + models.Value(1)),
                        name="inv_opening_cutoff_next_date",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="OpeningStockLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lines",
                        to="inventory.openingstockbatch",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_openingstockline_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="opening_stock_lines",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="opening_stock_lines",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_opening_stock_line",
                "ordering": ["product__sku", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "product"),
                        name="inv_opening_line_product_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(quantity__gte=0),
                        name="inv_opening_line_qty_nonnegative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="StockMovement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("opening", "Abertura de estoque")], max_length=16)),
                ("operational_date", models.DateField()),
                ("quantity_delta", models.DecimalField(decimal_places=3, max_digits=14)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_stock_movements_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_stockmovement_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "opening_line",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="movement",
                        to="inventory.openingstockline",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_stock_movement",
                "ordering": ["operational_date", "created_at", "id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(kind="opening")
                        | (models.Q(quantity_delta__gte=0) & models.Q(opening_line__isnull=False)),
                        name="inv_opening_movement_nonnegative",
                    ),
                ],
            },
        ),
        migrations.RunPython(enable_inventory_rls, disable_inventory_rls),
    ]
