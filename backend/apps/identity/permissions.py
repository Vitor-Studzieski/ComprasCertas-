"""Permission helpers for identity assurance."""

from django.db import transaction
from rest_framework.permissions import BasePermission

from apps.identity.db_context import set_rls_context
from apps.identity.models import Membership
from apps.identity.services.authentication import PRIVILEGED_ROLES


class MfaForPrivilegedUsers(BasePermission):
    """Require an MFA-authenticated session while any privileged membership is active."""

    message = "Conclua a verificação multifator antes de continuar."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.session.get("mfa_verified") is True:
            return True

        with transaction.atomic():
            set_rls_context(user_id=user.id)
            has_privileged_membership = Membership.objects.filter(
                user_id=user.id,
                status=Membership.Status.ACTIVE,
                role__in=PRIVILEGED_ROLES,
            ).exists()
        return not has_privileged_membership
