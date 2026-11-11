"""Transaction-local PostgreSQL RLS context helpers."""

from django.db import connection


def set_rls_context(*, organization_id=None, user_id=None, invitation_token_hash=None) -> None:
    """Set tenant/user context for the current PostgreSQL transaction only."""
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError("RLS context must be set inside transaction.atomic().")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_organization_id', %s, true), "
            "set_config('app.current_user_id', %s, true), "
            "set_config('app.invitation_token_hash', %s, true)",
            [
                str(organization_id or ""),
                str(user_id or ""),
                str(invitation_token_hash or ""),
            ],
        )


def set_invitation_token_context(*, token_hash: str) -> None:
    """Allow lookup of an invitation by its hashed bearer token."""
    if not connection.in_atomic_block:
        raise RuntimeError("Invitation token context must be set inside transaction.atomic().")
    set_rls_context(invitation_token_hash=token_hash)
