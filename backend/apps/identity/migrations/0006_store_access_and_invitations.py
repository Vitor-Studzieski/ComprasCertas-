"""Add tenant-scoped store assignments and seven-day invitations."""

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLES = (
    "identity_store_access_assignment",
    "identity_organization_invitation",
    "identity_invitation_store_scope",
    "identity_membership_access_event",
)


def _tenant_predicate(table):
    return (
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "AND EXISTS (SELECT 1 FROM identity_membership AS active_membership "
        f"WHERE active_membership.organization_id = {table}.organization_id "
        "AND active_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND active_membership.status = 'active')"
    )


def enable_access_rls(apps, schema_editor):
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
            if table != "identity_membership_access_event":
                cursor.execute(
                    f'CREATE POLICY "{table}_insert_active_member" ON "{table}" '
                    f"FOR INSERT WITH CHECK ({predicate})"
                )
            if table in ("identity_store_access_assignment", "identity_organization_invitation"):
                cursor.execute(
                    f'CREATE POLICY "{table}_update_active_member" ON "{table}" '
                    f"FOR UPDATE USING ({predicate}) WITH CHECK ({predicate})"
                )

        cursor.execute(
            'CREATE POLICY "identity_invitation_token_lookup" '
            'ON "identity_organization_invitation" FOR SELECT USING '
            "(token_hash = NULLIF(current_setting('app.invitation_token_hash', true), ''))"
        )
        cursor.execute(
            'CREATE POLICY "identity_invitation_token_accept" '
            'ON "identity_organization_invitation" FOR UPDATE USING '
            "(token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '') "
            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()) "
            'WITH CHECK (token_hash = NULLIF(current_setting(\'app.invitation_token_hash\', true), \'\'))'
        )
        cursor.execute(
            'CREATE POLICY "identity_invitation_scope_token_lookup" '
            'ON "identity_invitation_store_scope" FOR SELECT USING '
            "(EXISTS (SELECT 1 FROM identity_organization_invitation AS invited "
            "WHERE invited.id = identity_invitation_store_scope.invitation_id "
            "AND invited.token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '')))"
        )
        cursor.execute(
            'CREATE POLICY "identity_org_invitation_token_lookup" '
            'ON "identity_organization" FOR SELECT USING '
            "(EXISTS (SELECT 1 FROM identity_organization_invitation AS invited "
            "WHERE invited.organization_id = identity_organization.id "
            "AND invited.token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '')))"
        )
        cursor.execute(
            'CREATE POLICY "inventory_store_invitation_token_lookup" '
            'ON "inventory_store" FOR SELECT USING '
            "(EXISTS (SELECT 1 FROM identity_invitation_store_scope AS scope "
            "JOIN identity_organization_invitation AS invited ON invited.id = scope.invitation_id "
            "WHERE scope.store_id = inventory_store.id "
            "AND scope.organization_id = inventory_store.organization_id "
            "AND invited.token_hash = NULLIF(current_setting('app.invitation_token_hash', true), '')))"
        )
        cursor.execute(
            'CREATE POLICY "identity_membership_access_event_insert_active_member" '
            'ON "identity_membership_access_event" FOR INSERT WITH CHECK '
            f"({_tenant_predicate('identity_membership_access_event')})"
        )


def disable_access_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            'DROP POLICY IF EXISTS "identity_invitation_token_lookup" '
            'ON "identity_organization_invitation"'
        )
        cursor.execute(
            'DROP POLICY IF EXISTS "identity_invitation_token_accept" '
            'ON "identity_organization_invitation"'
        )
        cursor.execute(
            'DROP POLICY IF EXISTS "identity_invitation_scope_token_lookup" '
            'ON "identity_invitation_store_scope"'
        )
        cursor.execute(
            'DROP POLICY IF EXISTS "identity_org_invitation_token_lookup" '
            'ON "identity_organization"'
        )
        cursor.execute(
            'DROP POLICY IF EXISTS "inventory_store_invitation_token_lookup" '
            'ON "inventory_store"'
        )
        for table in reversed(TABLES):
            for action in ("select", "insert", "update"):
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
            name="StoreAccessAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("assigned_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="store_access_assignments_created", to=settings.AUTH_USER_MODEL)),
                ("membership", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="store_assignments", to="identity.membership")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="store_access_assignments", to="identity.organization")),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="store_access_assignments_revoked", to=settings.AUTH_USER_MODEL)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="access_assignments", to="inventory.store")),
            ],
            options={"db_table": "identity_store_access_assignment", "ordering": ["organization_id", "membership_id", "store_id", "assigned_at"]},
        ),
        migrations.CreateModel(
            name="OrganizationInvitation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(max_length=254)),
                ("role", models.CharField(choices=[("administrator", "Administrador da rede"), ("store_manager", "Gerente de loja"), ("operator", "Operador"), ("buyer", "Comprador/aprovador")], max_length=24)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("sent_count", models.PositiveSmallIntegerField(default=1)),
                ("last_sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("accepted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="organization_invitations_accepted", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="organization_invitations_created", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="invitations", to="identity.organization")),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="organization_invitations_revoked", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "identity_organization_invitation", "ordering": ["-created_at", "id"]},
        ),
        migrations.CreateModel(
            name="OrganizationInvitationStoreScope",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("invitation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="store_scopes", to="identity.organizationinvitation")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="invitation_store_scopes", to="identity.organization")),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="invitation_scopes", to="inventory.store")),
            ],
            options={"db_table": "identity_invitation_store_scope", "ordering": ["invitation_id", "store_id"]},
        ),
        migrations.CreateModel(
            name="MembershipAccessEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(max_length=32)),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="membership_access_events_created", to=settings.AUTH_USER_MODEL)),
                ("invitation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="access_events", to="identity.organizationinvitation")),
                ("membership", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="access_events", to="identity.membership")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="membership_access_events", to="identity.organization")),
            ],
            options={"db_table": "identity_membership_access_event", "ordering": ["created_at", "id"]},
        ),
        migrations.AddField(
            model_name="organizationinvitation",
            name="stores",
            field=models.ManyToManyField(related_name="organization_invitations", through="identity.organizationinvitationstorescope", to="inventory.store"),
        ),
        migrations.AddConstraint(
            model_name="storeaccessassignment",
            constraint=models.UniqueConstraint(condition=models.Q(("revoked_at__isnull", True)), fields=("organization", "membership", "store"), name="identity_store_access_active_unique"),
        ),
        migrations.AddConstraint(
            model_name="storeaccessassignment",
            constraint=models.CheckConstraint(condition=(models.Q(("revoked_at__isnull", True), ("revoked_by__isnull", True)) | models.Q(("revoked_at__isnull", False), ("revoked_by__isnull", False))), name="identity_store_access_revocation_actor_valid"),
        ),
        migrations.AddConstraint(
            model_name="organizationinvitationstorescope",
            constraint=models.UniqueConstraint(fields=("organization", "invitation", "store"), name="identity_inv_scope_unique"),
        ),
        migrations.AddConstraint(
            model_name="organizationinvitation",
            constraint=models.CheckConstraint(condition=(models.Q(("accepted_at__isnull", True), ("accepted_by__isnull", True)) | models.Q(("accepted_at__isnull", False), ("accepted_by__isnull", False))), name="identity_inv_accept_actor_valid"),
        ),
        migrations.AddConstraint(
            model_name="organizationinvitation",
            constraint=models.CheckConstraint(condition=(models.Q(("revoked_at__isnull", True), ("revoked_by__isnull", True)) | models.Q(("revoked_at__isnull", False), ("revoked_by__isnull", False))), name="identity_inv_revoke_actor_valid"),
        ),
        migrations.AddConstraint(
            model_name="membershipaccessevent",
            constraint=models.CheckConstraint(condition=(models.Q(("membership__isnull", False), ("invitation__isnull", True)) | models.Q(("membership__isnull", True), ("invitation__isnull", False))), name="identity_access_event_one_subject"),
        ),
        migrations.AddIndex(
            model_name="organizationinvitation",
            index=models.Index(fields=["organization", "email"], name="identity_inv_org_email_idx"),
        ),
        migrations.AddIndex(
            model_name="organizationinvitation",
            index=models.Index(fields=["expires_at"], name="identity_inv_expiry_idx"),
        ),
        migrations.RunPython(enable_access_rls, disable_access_rls),
    ]
