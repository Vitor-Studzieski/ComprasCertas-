"""Create short-lived, tenant-scoped spreadsheet import previews."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def enable_import_batch_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('ALTER TABLE "imports_batch" ENABLE ROW LEVEL SECURITY')
        cursor.execute('ALTER TABLE "imports_batch" FORCE ROW LEVEL SECURITY')
        predicate = (
            "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
            "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
            "WHERE active_membership.organization_id = imports_batch.organization_id "
            "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
            "AND active_membership.status = 'active')"
        )
        cursor.execute(
            'CREATE POLICY "imports_batch_select_active_member" ON "imports_batch" '
            f"FOR SELECT USING ({predicate})"
        )
        cursor.execute(
            'CREATE POLICY "imports_batch_insert_active_member" ON "imports_batch" '
            f"FOR INSERT WITH CHECK ({predicate})"
        )
        cursor.execute(
            'CREATE POLICY "imports_batch_update_active_member" ON "imports_batch" '
            f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
        )


def disable_import_batch_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for action in ("select", "insert", "update"):
            cursor.execute(
                f'DROP POLICY IF EXISTS "imports_batch_{action}_active_member" ON "imports_batch"'
            )
        cursor.execute('ALTER TABLE "imports_batch" NO FORCE ROW LEVEL SECURITY')
        cursor.execute('ALTER TABLE "imports_batch" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("catalog", "0001_initial"),
        ("identity", "0006_store_access_and_invitations"),
        ("inventory", "0004_purchase_receipt_movements"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ImportBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("products", "Produtos"), ("suppliers", "Fornecedores"), ("conditions", "Condições de compra"), ("opening_stock", "Estoque inicial"), ("daily_sales", "Vendas diárias"), ("out_of_stock_days", "Dias sem estoque")], max_length=24)),
                ("status", models.CharField(choices=[("preview", "Prévia"), ("confirmed", "Confirmada"), ("expired", "Expirada")], default="preview", max_length=16)),
                ("file_name", models.CharField(max_length=255)),
                ("file_sha256", models.CharField(max_length=64)),
                ("rows", models.JSONField(default=list)),
                ("metadata", models.JSONField(default=dict)),
                ("preview", models.JSONField(default=dict)),
                ("target_fingerprint", models.CharField(blank=True, default="", max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spreadsheet_import_batches", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spreadsheet_import_batches", to="identity.organization")),
            ],
            options={"db_table": "imports_batch", "ordering": ["-created_at", "id"]},
        ),
        migrations.AddIndex(model_name="importbatch", index=models.Index(fields=["organization", "created_by", "status"], name="imports_batch_owner_idx")),
        migrations.AddIndex(model_name="importbatch", index=models.Index(fields=["expires_at"], name="imports_batch_expiry_idx")),
        migrations.RunPython(enable_import_batch_rls, disable_import_batch_rls),
    ]
