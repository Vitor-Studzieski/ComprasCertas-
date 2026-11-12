"""Add confirmed expected deliveries and ETA risk alerts."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLES = (
    "purchasing_expected_delivery",
    "purchasing_expected_delivery_event",
    "purchasing_delivery_risk_alert",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_rls(apps, schema_editor):
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
            if table != "purchasing_expected_delivery_event":
                cursor.execute(
                    f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
                    f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
                )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in reversed(TABLES):
            for action in ("select", "insert", "update"):
                cursor.execute(f'DROP POLICY IF EXISTS "{table}_{action}_active_member" ON "{table}"')
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0001_initial"),
        ("identity", "0005_preauthchallenge_recoverycode_webauthncredential"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="sent_to_supplier_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="sent_to_supplier_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_orders_sent",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="ExpectedDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("eta_date", models.DateField()),
                ("expected_quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("received_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("status", models.CharField(choices=[("confirmed", "Confirmada"), ("received", "Recebida"), ("cancelled", "Cancelada")], default="confirmed", max_length=12)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expected_deliveries_confirmed", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_expecteddelivery_records", to="identity.organization")),
                ("order_item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expected_deliveries", to="purchasing.purchaseorderitem")),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expected_deliveries_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "purchasing_expected_delivery", "ordering": ["eta_date", "created_at", "id"]},
        ),
        migrations.CreateModel(
            name="ExpectedDeliveryEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=32)),
                ("old_eta_date", models.DateField(blank=True, null=True)),
                ("new_eta_date", models.DateField(blank=True, null=True)),
                ("reason", models.TextField(blank=True, default="")),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expected_delivery_events_created", to=settings.AUTH_USER_MODEL)),
                ("delivery", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="purchasing.expecteddelivery")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_expecteddeliveryevent_records", to="identity.organization")),
            ],
            options={"db_table": "purchasing_expected_delivery_event", "ordering": ["created_at", "id"]},
        ),
        migrations.CreateModel(
            name="DeliveryRiskAlert",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("old_eta_date", models.DateField()),
                ("new_eta_date", models.DateField()),
                ("previous_stockout_date", models.DateField(blank=True, null=True)),
                ("projected_stockout_date", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("open", "Aberto"), ("acknowledged", "Reconhecido"), ("resolved", "Resolvido"), ("justified", "Justificado")], default="open", max_length=16)),
                ("action_note", models.TextField(blank=True, default="")),
                ("resolution_reason", models.TextField(blank=True, default="")),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("acknowledged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="delivery_risk_alerts_acknowledged", to=settings.AUTH_USER_MODEL)),
                ("delivery", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="risk_alerts", to="purchasing.expecteddelivery")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchasing_deliveryriskalert_records", to="identity.organization")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="delivery_risk_alerts_resolved", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "purchasing_delivery_risk_alert", "ordering": ["-created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="expecteddelivery",
            constraint=models.CheckConstraint(condition=models.Q(("expected_quantity__gt", 0)), name="pur_expected_qty_positive"),
        ),
        migrations.AddConstraint(
            model_name="expecteddelivery",
            constraint=models.CheckConstraint(condition=models.Q(("received_quantity__gte", 0)), name="pur_expected_received_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="expecteddelivery",
            constraint=models.CheckConstraint(condition=models.Q(("received_quantity__lte", models.F("expected_quantity"))), name="pur_expected_received_lte_expected"),
        ),
        migrations.AddConstraint(
            model_name="expecteddelivery",
            constraint=models.CheckConstraint(condition=models.Q(("status__in", ["confirmed", "received", "cancelled"])), name="pur_expected_status_valid"),
        ),
        migrations.RunPython(enable_rls, disable_rls),
    ]
