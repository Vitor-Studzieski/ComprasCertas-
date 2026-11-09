"""Transaction-local PostgreSQL RLS context helpers."""

from django.db import connection


def set_rls_context(*, organization_id=None, user_id=None) -> None:
    """Set tenant/user context for the current PostgreSQL transaction only."""
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError("RLS context must be set inside transaction.atomic().")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_organization_id', %s, true), "
            "set_config('app.current_user_id', %s, true)",
            [str(organization_id or ""), str(user_id or "")],
        )
