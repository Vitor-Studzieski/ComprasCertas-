"""Apply row-level isolation to organization-scoped identity tables."""

from django.db import migrations


POLICIES = [
    (
        "identity_organization",
        "identity_organization_read_scope",
        "id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "OR EXISTS (SELECT 1 FROM identity_membership AS own_membership "
        "WHERE own_membership.organization_id = identity_organization.id "
        "AND own_membership.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid "
        "AND own_membership.status = 'active')",
        None,
        "SELECT",
    ),
    (
        "identity_organization",
        "identity_organization_write_scope",
        "id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "ALL",
    ),
    (
        "identity_membership",
        "identity_membership_read_scope",
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid "
        "OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid",
        None,
        "SELECT",
    ),
    (
        "identity_membership",
        "identity_membership_write_scope",
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "ALL",
    ),
    (
        "identity_trial",
        "identity_trial_scope",
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid",
        "ALL",
    ),
]


def enable_tenant_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table in {policy[0] for policy in POLICIES}:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

        for table, name, using, check, command in POLICIES:
            policy_sql = f'CREATE POLICY "{name}" ON "{table}" FOR {command} USING ({using})'
            if check is not None:
                policy_sql += f' WITH CHECK ({check})'
            cursor.execute(policy_sql)


def disable_tenant_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        for table, name, _, _, _ in reversed(POLICIES):
            cursor.execute(f'DROP POLICY IF EXISTS "{name}" ON "{table}"')
        for table in {policy[0] for policy in POLICIES}:
            cursor.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0003_remove_pendingregistration_confirmation_token_hash_and_more"),
    ]

    operations = [migrations.RunPython(enable_tenant_rls, disable_tenant_rls)]
