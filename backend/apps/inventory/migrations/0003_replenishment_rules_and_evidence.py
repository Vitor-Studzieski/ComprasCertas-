"""Add replenishment policies, categories, and explicit demand evidence."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLES = (
    "inventory_product_category",
    "inventory_replenishment_policy",
    "inventory_daily_sales_coverage",
    "inventory_daily_stock_availability",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_replenishment_rls(apps, schema_editor):
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
        table = "inventory_replenishment_policy"
        predicate = _tenant_predicate(table)
        cursor.execute(
            f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
            f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
        )


def disable_replenishment_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            'DROP POLICY IF EXISTS "inventory_replenishment_policy_update_active_member" '
            'ON "inventory_replenishment_policy"'
        )
        for table in reversed(TABLES):
            for action in ("select", "insert"):
                cursor.execute(
                    f'DROP POLICY IF EXISTS "{table}_{action}_active_member" ON "{table}"'
                )
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("identity", "0005_preauthchallenge_recoverycode_webauthncredential"),
        ("inventory", "0002_daily_sales_and_corrections"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductCategory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_productcategory_records",
                        to="identity.organization",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_product_category",
                "ordering": ["name", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "name"),
                        name="inv_category_org_name_unique",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="product",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                to="inventory.productcategory",
            ),
        ),
        migrations.CreateModel(
            name="ReplenishmentPolicy",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("minimum_stock", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("safety_days", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("review_interval_days", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replenishment_policies",
                        to="inventory.productcategory",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_replenishment_policies_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_replenishmentpolicy_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replenishment_policies",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replenishment_policies",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_replenishment_policy",
                "ordering": ["organization_id", "store_id", "product_id", "category_id", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization",),
                        condition=models.Q(("category__isnull", True), ("product__isnull", True), ("store__isnull", True)),
                        name="inv_replenishment_network_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "category"),
                        condition=models.Q(("category__isnull", False), ("product__isnull", True), ("store__isnull", True)),
                        name="inv_replenishment_category_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "product"),
                        condition=models.Q(("product__isnull", False), ("category__isnull", True), ("store__isnull", True)),
                        name="inv_replenishment_product_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "store"),
                        condition=models.Q(("store__isnull", False), ("product__isnull", True), ("category__isnull", True)),
                        name="inv_replenishment_store_unique",
                    ),
                    models.UniqueConstraint(
                        fields=("organization", "store", "product"),
                        condition=models.Q(("store__isnull", False), ("product__isnull", False), ("category__isnull", True)),
                        name="inv_replenishment_store_product_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("minimum_stock__isnull", True)) | models.Q(("minimum_stock__gte", 0)),
                        name="inv_replenishment_min_nonnegative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("category__isnull", True)) | models.Q(("product__isnull", True), ("store__isnull", True)),
                        name="inv_replenishment_category_scope_valid",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(("store__isnull", True), ("product__isnull", True), ("category__isnull", True))
                            | models.Q(("store__isnull", False), ("product__isnull", False), ("category__isnull", True), ("review_interval_days__isnull", True))
                            | models.Q(("store__isnull", True), ("product__isnull", True), ("category__isnull", False), ("review_interval_days__isnull", True))
                            | models.Q(("store__isnull", True), ("product__isnull", False), ("category__isnull", True), ("review_interval_days__isnull", True))
                            | models.Q(("store__isnull", False), ("product__isnull", True), ("category__isnull", True), ("minimum_stock__isnull", True), ("safety_days__isnull", True))
                        ),
                        name="inv_replenishment_fields_scope_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("review_interval_days__isnull", True)) | models.Q(("review_interval_days__gte", 1)),
                        name="inv_replenishment_review_interval_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("safety_days__isnull", True)) | models.Q(("safety_days__lte", 365)),
                        name="inv_replenishment_safety_days_max",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DailySalesCoverage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operational_date_from", models.DateField()),
                ("operational_date_to", models.DateField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_sales_coverage_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_dailysalescoverage_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_coverage_periods",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_daily_sales_coverage",
                "ordering": ["store_id", "operational_date_from", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store", "operational_date_from", "operational_date_to"),
                        name="inv_sales_coverage_period_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("operational_date_to__gte", models.F("operational_date_from"))),
                        name="inv_sales_coverage_range_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="DailyStockAvailability",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operational_date", models.DateField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_stock_availability_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory_dailystockavailability_records",
                        to="identity.organization",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="daily_stock_availability",
                        to="inventory.product",
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="daily_stock_availability",
                        to="inventory.store",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_daily_stock_availability",
                "ordering": ["store_id", "product_id", "operational_date", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "store", "product", "operational_date"),
                        name="inv_stock_availability_day_unique",
                    ),
                ],
            },
        ),
        migrations.RunPython(enable_replenishment_rls, disable_replenishment_rls),
    ]
