"""Add purchase budgets, order snapshots, receipts, and tenant RLS."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLES = (
    "purchasing_budget",
    "purchasing_order",
    "purchasing_order_item",
    "purchasing_receipt",
    "purchasing_receipt_line",
    "purchasing_order_event",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_purchasing_rls(apps, schema_editor):
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
        for table in ("purchasing_budget", "purchasing_order", "purchasing_order_item", "purchasing_receipt_line"):
            predicate = _tenant_predicate(table)
            cursor.execute(
                f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
                f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
            )


def disable_purchasing_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in reversed(TABLES):
            for action in ("select", "insert", "update"):
                cursor.execute(
                    f'DROP POLICY IF EXISTS "{table}_{action}_active_member" ON "{table}"'
                )
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0001_initial"),
        ("identity", "0005_preauthchallenge_recoverycode_webauthncredential"),
        ("inventory", "0003_replenishment_rules_and_evidence"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseBudget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("weekly_limit", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("monthly_limit", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("week_starts_on", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_budgets_created", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchasebudget_records", to="identity.organization")),
                ("store", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_budgets", to="inventory.store")),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_budgets_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "purchasing_budget", "ordering": ["organization_id", "store_id", "id"]},
        ),
        migrations.CreateModel(
            name="PurchaseOrder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("draft", "Rascunho"), ("reviewed", "Revisado"), ("approved", "Aprovado"), ("partially_received", "Recebido parcialmente"), ("received", "Recebido"), ("cancelled", "Cancelado"), ("stale", "Desatualizado")], default="draft", max_length=24)),
                ("source_snapshot", models.JSONField(default=dict)),
                ("approved_snapshot", models.JSONField(blank=True, null=True)),
                ("estimated_total", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("committed_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("budget_week_start", models.DateField(blank=True, null=True)),
                ("budget_month_start", models.DateField(blank=True, null=True)),
                ("budget_exception_reason", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders_approved", to=settings.AUTH_USER_MODEL)),
                ("cancelled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders_cancelled", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders_created", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchaseorder_records", to="identity.organization")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders_reviewed", to=settings.AUTH_USER_MODEL)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders", to="inventory.store")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders", to="catalog.supplier")),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_orders_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "purchasing_order", "ordering": ["-created_at", "id"]},
        ),
        migrations.CreateModel(
            name="PurchaseOrderItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("purchase_unit_name", models.CharField(max_length=40)),
                ("base_units_per_purchase_unit", models.DecimalField(decimal_places=3, max_digits=14)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("minimum_purchase_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("purchase_multiple", models.DecimalField(decimal_places=3, max_digits=14)),
                ("recommended_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("approved_quantity", models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ("received_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("source_snapshot", models.JSONField(default=dict)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="items", to="purchasing.purchaseorder")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchaseorderitem_records", to="identity.organization")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_order_items", to="inventory.product")),
                ("supplier_condition", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_order_items", to="catalog.supplierproductcondition")),
            ],
            options={"db_table": "purchasing_order_item", "ordering": ["product__sku", "id"]},
        ),
        migrations.CreateModel(
            name="PurchaseReceipt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("operational_date", models.DateField()),
                ("supplier_reference", models.CharField(blank=True, default="", max_length=120)),
                ("budget_exception_reason", models.TextField(blank=True, default="")),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchasereceipt_records", to="identity.organization")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipts", to="purchasing.purchaseorder")),
                ("received_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_receipts_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "purchasing_receipt", "ordering": ["-received_at", "id"]},
        ),
        migrations.CreateModel(
            name="PurchaseReceiptLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("physical_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("accepted_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("pending_excess_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("actual_unit_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("excess_decision", models.CharField(default="pending", max_length=12)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchasereceiptline_records", to="identity.organization")),
                ("order_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="receipt_lines", to="purchasing.purchaseorderitem")),
                ("receipt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="purchasing.purchasereceipt")),
            ],
            options={"db_table": "purchasing_receipt_line", "ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="PurchaseOrderEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=32)),
                ("reason", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_order_events_created", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_purchaseorderevent_records", to="identity.organization")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="purchasing.purchaseorder")),
            ],
            options={"db_table": "purchasing_order_event", "ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.CheckConstraint(condition=models.Q(("weekly_limit__isnull", True)) | models.Q(("weekly_limit__gte", 0)), name="pur_budget_week_nonnegative")),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.CheckConstraint(condition=models.Q(("monthly_limit__isnull", True)) | models.Q(("monthly_limit__gte", 0)), name="pur_budget_month_nonnegative")),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.CheckConstraint(condition=(models.Q(("store__isnull", True), ("week_starts_on__gte", 0), ("week_starts_on__lte", 6)) | models.Q(("store__isnull", False), ("week_starts_on__isnull", True))), name="pur_budget_week_scope_valid")),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.CheckConstraint(condition=(models.Q(("store__isnull", True), ("weekly_limit__isnull", False), ("monthly_limit__isnull", False)) | models.Q(("store__isnull", False))), name="pur_budget_network_limits_required")),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.UniqueConstraint(condition=models.Q(("store__isnull", True)), fields=("organization",), name="pur_budget_network_unique")),
        migrations.AddConstraint(model_name="purchasebudget", constraint=models.UniqueConstraint(condition=models.Q(("store__isnull", False)), fields=("organization", "store"), name="pur_budget_store_unique")),
        migrations.AddConstraint(model_name="purchaseorder", constraint=models.CheckConstraint(condition=models.Q(("estimated_total__gte", 0)), name="pur_order_total_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorder", constraint=models.CheckConstraint(condition=models.Q(("committed_amount__gte", 0)), name="pur_order_commit_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.UniqueConstraint(fields=("order", "product"), name="pur_order_item_product_unique")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("base_units_per_purchase_unit__gt", 0)), name="pur_item_conversion_positive")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("unit_price__gte", 0)), name="pur_item_price_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("minimum_purchase_quantity__gte", 0)), name="pur_item_min_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("purchase_multiple__gt", 0)), name="pur_item_multiple_positive")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("recommended_quantity__gte", 0)), name="pur_item_recommended_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="pur_item_quantity_positive")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("received_quantity__gte", 0)), name="pur_item_received_nonnegative")),
        migrations.AddConstraint(model_name="purchaseorderitem", constraint=models.CheckConstraint(condition=models.Q(("approved_quantity__isnull", True)) | models.Q(("approved_quantity__gte", models.F("received_quantity"))), name="pur_item_approved_covers_received")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("physical_quantity__gt", 0)), name="pur_receipt_physical_positive")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("accepted_quantity__gte", 0)), name="pur_receipt_accepted_nonnegative")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("pending_excess_quantity__gte", 0)), name="pur_receipt_excess_nonnegative")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("actual_unit_price__gte", 0)), name="pur_receipt_price_nonnegative")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("physical_quantity", models.F("accepted_quantity") + models.F("pending_excess_quantity"))), name="pur_receipt_quantities_reconcile")),
        migrations.AddConstraint(model_name="purchasereceiptline", constraint=models.CheckConstraint(condition=models.Q(("excess_decision__in", ["pending", "accepted", "rejected"])), name="pur_receipt_excess_decision_valid")),
        migrations.RunPython(enable_purchasing_rls, disable_purchasing_rls),
    ]
