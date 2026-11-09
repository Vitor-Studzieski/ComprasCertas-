"""Signup, email confirmation, and trial creation services."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.db_context import set_rls_context
from apps.identity.models import (
    Membership,
    Organization,
    PendingRegistration,
    PendingRegistrationToken,
    Trial,
    User,
    normalize_email_address,
)

logger = logging.getLogger(__name__)


class InvalidRegistrationToken(Exception):
    """The email confirmation token is missing, expired, or already used."""


@dataclass(frozen=True)
class ConfirmedRegistration:
    trial_starts_at: datetime
    trial_ends_at: datetime


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _send_confirmation_email(email: str, organization_name: str, token: str) -> None:
    confirmation_url = (
        f"{settings.FRONTEND_BASE_URL.rstrip('/')}/confirm-email#token={quote(token, safe='')}"
    )
    try:
        send_mail(
            subject="Confirme seu e-mail para ativar o CompraCerta",
            message=(
                f"Olá! Confirme o e-mail para iniciar o teste do CompraCerta para "
                f"{organization_name}:\n\n{confirmation_url}\n\n"
                f"Este link expira em {settings.REGISTRATION_TOKEN_TTL_HOURS} horas. "
                "Se você não solicitou o cadastro, "
                "ignore esta mensagem."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:
        # Signup remains generic; the user can request another confirmation email.
        logger.exception("Unable to deliver a CompraCerta confirmation email.")


def start_registration(
    *,
    organization_name: str,
    administrator_name: str,
    email: str,
    password: str,
) -> None:
    """Create/update a pending registration without creating a usable tenant."""
    normalized_email = normalize_email_address(email)
    now = timezone.now()
    token = _new_token()
    expires_at = now + timedelta(hours=settings.REGISTRATION_TOKEN_TTL_HOURS)

    try:
        with transaction.atomic():
            if User.objects.filter(email=normalized_email).exists():
                return

            pending = (
                PendingRegistration.objects.select_for_update()
                .filter(email=normalized_email)
                .first()
            )
            if pending and pending.expires_at > now:
                # Do not replace a pending signup's password or network details.
                return

            values = {
                "organization_name": organization_name,
                "administrator_name": administrator_name,
                "email": normalized_email,
                "password_hash": make_password(password),
                "expires_at": expires_at,
            }
            if pending:
                PendingRegistrationToken.objects.filter(registration=pending).delete()
                for field, value in values.items():
                    setattr(pending, field, value)
                pending.save()
            else:
                pending = PendingRegistration.objects.create(**values)

            PendingRegistrationToken.objects.create(
                registration=pending,
                token_hash=_hash_token(token),
                expires_at=expires_at,
            )
            transaction.on_commit(
                lambda: _send_confirmation_email(normalized_email, organization_name, token)
            )
    except IntegrityError:
        # A concurrent request may have won the unique-email insert race.
        if (
            User.objects.filter(email=normalized_email).exists()
            or PendingRegistration.objects.filter(email=normalized_email).exists()
        ):
            return
        raise


def resend_registration_confirmation(*, email: str) -> None:
    """Rotate and resend a pending token without revealing account state."""
    normalized_email = normalize_email_address(email)
    now = timezone.now()
    token = _new_token()

    with transaction.atomic():
        pending = (
            PendingRegistration.objects.select_for_update()
            .filter(email=normalized_email, expires_at__gt=now)
            .first()
        )
        if pending is None:
            return

        token_expires_at = now + timedelta(hours=settings.REGISTRATION_TOKEN_TTL_HOURS)
        pending.expires_at = token_expires_at
        pending.save(update_fields=["expires_at", "updated_at"])
        PendingRegistrationToken.objects.create(
            registration=pending,
            token_hash=_hash_token(token),
            expires_at=token_expires_at,
        )
        transaction.on_commit(
            lambda: _send_confirmation_email(
                pending.email,
                pending.organization_name,
                token,
            )
        )


def confirm_registration(*, token: str) -> ConfirmedRegistration:
    """Atomically create user, organization, admin membership, and 30-day trial."""
    token_hash = _hash_token(token)
    now = timezone.now()

    with transaction.atomic():
        issued_token = (
            PendingRegistrationToken.objects.select_for_update()
            .filter(token_hash=token_hash, expires_at__gt=now)
            .first()
        )
        if issued_token is None:
            raise InvalidRegistrationToken
        pending = (
            PendingRegistration.objects.select_for_update()
            .filter(pk=issued_token.registration_id, expires_at__gt=now)
            .first()
        )
        if pending is None:
            raise InvalidRegistrationToken

        user = User(
            email=pending.email,
            full_name=pending.administrator_name,
            email_verified_at=now,
            is_active=True,
        )
        # The pending value is already a Django password hash.
        user.password = pending.password_hash
        try:
            with transaction.atomic():
                user.save(force_insert=True)
        except IntegrityError as exc:
            # Another registration flow may have claimed the global email.
            raise InvalidRegistrationToken from exc

        organization_id = uuid.uuid4()
        organization = Organization(
            id=organization_id,
            name=pending.organization_name,
        )
        set_rls_context(organization_id=organization_id, user_id=user.id)
        organization.save(force_insert=True)

        Membership.objects.create(
            organization=organization,
            user=user,
            role=Membership.Role.ADMINISTRATOR,
            status=Membership.Status.ACTIVE,
        )

        trial_end = now + timedelta(days=30)
        Trial.objects.create(
            organization=organization,
            starts_at=now,
            ends_at=trial_end,
            status=Trial.Status.ACTIVE,
        )

        pending.delete()
        return ConfirmedRegistration(trial_starts_at=now, trial_ends_at=trial_end)
