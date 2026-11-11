"""Validated active-organization context for authenticated sessions."""

from uuid import UUID

from django.db import connection, transaction

from apps.identity.db_context import set_rls_context
from apps.identity.models import Membership, StoreAccessAssignment


ACTIVE_ORGANIZATION_SESSION_KEY = "active_organization_id"


class ActiveOrganizationRequired(Exception):
    """Raised when a request has no valid active organization in its session."""


def _active_membership_rows(user):
    return list(
        Membership.objects.filter(
            user_id=user.pk,
            status=Membership.Status.ACTIVE,
            organization__is_active=True,
        )
        .values("id", "organization_id", "organization__name", "role")
    )


def select_sole_active_organization(*, user, session) -> UUID | None:
    """Select the only active organization, or clear selection when ambiguous."""
    with transaction.atomic():
        set_rls_context(user_id=user.pk)
        rows = _active_membership_rows(user)

    if len(rows) == 1:
        organization_id = rows[0]["organization_id"]
        session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(organization_id)
        return organization_id

    session.pop(ACTIVE_ORGANIZATION_SESSION_KEY, None)
    return None


def get_current_session_state(*, user, session) -> dict:
    """List active memberships and clear an organization selection that went stale."""
    with transaction.atomic():
        set_rls_context(user_id=user.pk)
        rows = _active_membership_rows(user)
        store_scopes = {}
        for row in rows:
            organization_id = row["organization_id"]
            if row["role"] == Membership.Role.ADMINISTRATOR:
                store_scopes[row["id"]] = None
                continue
            set_rls_context(organization_id=organization_id, user_id=user.pk)
            store_scopes[row["id"]] = [
                str(store_id)
                for store_id in StoreAccessAssignment.objects.filter(
                    organization_id=organization_id,
                    membership_id=row["id"],
                    revoked_at__isnull=True,
                ).values_list("store_id", flat=True)
            ]

    active_id = None
    raw_active_id = session.get(ACTIVE_ORGANIZATION_SESSION_KEY)
    try:
        candidate_id = UUID(str(raw_active_id)) if raw_active_id else None
    except (TypeError, ValueError, AttributeError):
        candidate_id = None

    if candidate_id and any(row["organization_id"] == candidate_id for row in rows):
        active_id = candidate_id
    elif len(rows) == 1:
        # Also repair sessions created before active-organization context was
        # introduced (or whose cached selection became invalid).
        active_id = rows[0]["organization_id"]
        session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(active_id)
    else:
        if raw_active_id is not None:
            session.pop(ACTIVE_ORGANIZATION_SESSION_KEY, None)

    return {
        "status": (
            "organization_selection_required"
            if rows and active_id is None
            else "authenticated"
        ),
        "activeOrganizationId": active_id,
        "organizations": [
            {
                "id": row["organization_id"],
                "name": row["organization__name"],
                "role": row["role"],
                "storeIds": store_scopes[row["id"]],
            }
            for row in rows
        ],
    }


def select_active_organization(*, user, session, organization_id: UUID) -> bool:
    """Persist selection only when the user has an active membership in that org."""
    with transaction.atomic():
        set_rls_context(user_id=user.pk)
        is_member = Membership.objects.filter(
            user_id=user.pk,
            organization_id=organization_id,
            status=Membership.Status.ACTIVE,
            organization__is_active=True,
        ).exists()

    if not is_member:
        return False

    session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(organization_id)
    return True


def set_active_organization_rls_context(*, user, session) -> UUID:
    """Validate the session's active org and set RLS context inside atomic().

    Tenant API views should call this helper inside their transaction before
    accessing tenant-scoped models. The UUID returned here has been checked
    against the authenticated user's current active membership; a request body
    organization ID must never be used as a substitute.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Active organization context must be set inside transaction.atomic().")

    set_rls_context(user_id=user.pk)
    raw_active_id = session.get(ACTIVE_ORGANIZATION_SESSION_KEY)
    try:
        organization_id = UUID(str(raw_active_id)) if raw_active_id else None
    except (TypeError, ValueError, AttributeError):
        organization_id = None

    is_member = bool(
        organization_id
        and Membership.objects.filter(
            user_id=user.pk,
            organization_id=organization_id,
            status=Membership.Status.ACTIVE,
            organization__is_active=True,
        ).exists()
    )
    if not is_member:
        if raw_active_id is not None:
            session.pop(ACTIVE_ORGANIZATION_SESSION_KEY, None)
        raise ActiveOrganizationRequired

    set_rls_context(organization_id=organization_id, user_id=user.pk)
    return organization_id
