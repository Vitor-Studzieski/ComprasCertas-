"""Password login and role-based MFA requirement decisions."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone

from apps.identity.db_context import set_rls_context
from apps.identity.models import (
    Membership,
    PreAuthChallenge,
    RecoveryCode,
    User,
    WebAuthnCredential,
)

PRIVILEGED_ROLES = (
    Membership.Role.ADMINISTRATOR,
    Membership.Role.STORE_MANAGER,
    Membership.Role.BUYER,
)


class InvalidCredentials(Exception):
    """Email/password is invalid or the global email is not confirmed."""


@dataclass(frozen=True)
class LoginState:
    user: User
    status: str
    challenge_id: UUID | None = None
    methods: tuple[str, ...] = ()


def start_password_login(*, request, email: str, password: str) -> LoginState:
    """Verify the password and create a non-session MFA challenge if needed."""
    user = authenticate(request=request, username=email, password=password)
    if user is None or user.email_verified_at is None or not user.is_active:
        raise InvalidCredentials

    with transaction.atomic():
        set_rls_context(user_id=user.id)
        requires_mfa = Membership.objects.filter(
            user=user,
            status=Membership.Status.ACTIVE,
            role__in=PRIVILEGED_ROLES,
        ).exists()
        if not requires_mfa:
            return LoginState(user=user, status="authenticated")

        has_credentials = WebAuthnCredential.objects.filter(user=user, is_active=True).exists()
        has_recovery_codes = RecoveryCode.objects.filter(
            user=user,
            used_at__isnull=True,
        ).exists()
        if has_credentials or has_recovery_codes:
            purpose = PreAuthChallenge.Purpose.AUTHENTICATION
            status = "second_factor_required"
            methods = tuple(
                method
                for available, method in (
                    (has_credentials, "webauthn"),
                    (has_recovery_codes, "recovery_code"),
                )
                if available
            )
        else:
            purpose = PreAuthChallenge.Purpose.ENROLLMENT
            status = "second_factor_enrollment_required"
            methods = ("webauthn",)

        challenge = PreAuthChallenge.objects.create(
            user=user,
            purpose=purpose,
            expires_at=timezone.now()
            + timedelta(minutes=settings.PRE_AUTH_CHALLENGE_TTL_MINUTES),
        )
        return LoginState(
            user=user,
            status=status,
            challenge_id=challenge.id,
            methods=methods,
        )


def parse_challenge_id(value: str | UUID) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidCredentials from exc
