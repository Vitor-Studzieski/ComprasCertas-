"""Tenant and store scope authorization helpers."""

from apps.identity.models import Membership, StoreAccessAssignment


class StoreAccessDenied(Exception):
    """The active membership does not have the requested store scope."""


def require_store_access(*, organization_id, user, store_id, allowed_roles):
    membership = Membership.objects.filter(
        organization_id=organization_id,
        user_id=user.pk,
        status=Membership.Status.ACTIVE,
    ).first()
    if membership is None:
        raise StoreAccessDenied
    if membership.role == Membership.Role.ADMINISTRATOR:
        return membership
    if membership.role not in allowed_roles:
        raise StoreAccessDenied
    if not StoreAccessAssignment.objects.filter(
        organization_id=organization_id,
        membership=membership,
        store_id=store_id,
        revoked_at__isnull=True,
    ).exists():
        raise StoreAccessDenied
    return membership


def active_store_ids(*, organization_id, membership):
    if membership.role == Membership.Role.ADMINISTRATOR:
        return None
    return list(
        StoreAccessAssignment.objects.filter(
            organization_id=organization_id,
            membership=membership,
            revoked_at__isnull=True,
        ).values_list("store_id", flat=True)
    )
