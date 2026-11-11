"""Invitation delivery, acceptance, and auditable store scope management."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.db_context import set_invitation_token_context, set_rls_context
from apps.identity.models import (
    Membership,
    MembershipAccessEvent,
    Organization,
    OrganizationInvitation,
    OrganizationInvitationStoreScope,
    PreAuthChallenge,
    RecoveryCode,
    StoreAccessAssignment,
    User,
    WebAuthnCredential,
    normalize_email_address,
)
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    select_active_organization,
    set_active_organization_rls_context,
)
from apps.inventory.models import Store


logger = logging.getLogger(__name__)
INVITATION_TTL_DAYS = 7


class AccessFlowError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _record_event(*, organization_id, actor, event_type, payload, membership=None, invitation=None):
    MembershipAccessEvent.objects.create(
        organization_id=organization_id,
        membership=membership,
        invitation=invitation,
        actor=actor,
        event_type=event_type,
        payload=payload,
    )


def _send_invitation_email(*, email, organization_name, role_name, store_names, token):
    accept_url = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/accept-invitation#token={quote(token, safe='')}"
    scope = "toda a rede" if role_name == "Administrador da rede" else ", ".join(store_names)
    try:
        send_mail(
            subject="Convite para o CompraCerta",
            message=(
                f"Você foi convidado(a) para acessar a rede {organization_name} "
                f"como {role_name}. Escopo de lojas: {scope}.\n\n"
                f"Aceite o convite neste link:\n{accept_url}\n\n"
                "O link é de uso único e expira em 7 dias."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Unable to deliver a CompraCerta organization invitation.")


def _invitation_repr(invitation):
    scopes = invitation.store_scopes.select_related("store").order_by("store__name", "store_id")
    return {
        "id": str(invitation.pk),
        "email": invitation.email,
        "role": invitation.role,
        "roleName": invitation.get_role_display(),
        "status": invitation.status,
        "storeIds": [str(row.store_id) for row in scopes],
        "storeNames": [row.store.name for row in scopes],
        "expiresAt": invitation.expires_at.isoformat(),
        "sentCount": invitation.sent_count,
        "createdAt": invitation.created_at.isoformat(),
        "acceptedAt": invitation.accepted_at.isoformat() if invitation.accepted_at else None,
        "revokedAt": invitation.revoked_at.isoformat() if invitation.revoked_at else None,
    }


def _membership_repr(*, organization_id, membership):
    assignments = StoreAccessAssignment.objects.filter(
        organization_id=organization_id,
        membership=membership,
        revoked_at__isnull=True,
    ).select_related("store").order_by("store__name", "store_id")
    return {
        "id": str(membership.pk),
        "userId": str(membership.user_id),
        "email": membership.user.email,
        "fullName": membership.user.full_name,
        "role": membership.role,
        "status": membership.status,
        "storeIds": [str(row.store_id) for row in assignments],
        "storeNames": [row.store.name for row in assignments],
    }


def list_memberships(*, request):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    memberships = Membership.objects.filter(organization_id=organization_id).select_related("user")
    return {
        "memberships": [
            _membership_repr(organization_id=organization_id, membership=row)
            for row in memberships
        ]
    }


def list_invitations(*, request):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    invitations = OrganizationInvitation.objects.filter(
        organization_id=organization_id,
    ).select_related("organization")
    return {"invitations": [_invitation_repr(row) for row in invitations]}


@transaction.atomic
def create_invitation(*, request, email, role, store_ids):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    normalized_email = normalize_email_address(email)
    organization = Organization.objects.select_for_update().get(pk=organization_id)
    store_ids = list(dict.fromkeys(store_ids))
    if role not in OrganizationInvitation.Role.values:
        raise AccessFlowError("invitation_role_invalid", "O papel informado não é válido.")
    if role == Membership.Role.ADMINISTRATOR and store_ids:
        raise AccessFlowError("invitation_scope_invalid", "O administrador recebe escopo de toda a rede.")
    if role != Membership.Role.ADMINISTRATOR and not store_ids:
        raise AccessFlowError("invitation_scope_required", "Selecione ao menos uma loja para este papel.")
    if Membership.objects.filter(
        organization_id=organization_id,
        user__email=normalized_email,
        status=Membership.Status.ACTIVE,
    ).exists():
        raise AccessFlowError("membership_already_active", "A pessoa já tem acesso ativo a esta rede.", 409)
    existing_invitation = OrganizationInvitation.objects.select_for_update().filter(
        organization_id=organization_id,
        email=normalized_email,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if existing_invitation:
        raise AccessFlowError("invitation_pending", "Já existe um convite válido para este e-mail.", 409)
    stores = list(Store.objects.filter(
        organization_id=organization_id,
        is_active=True,
        pk__in=store_ids,
    ))
    if len(stores) != len(store_ids):
        raise AccessFlowError("store_scope_invalid", "Uma ou mais lojas não estão disponíveis nesta rede.", 404)

    token = _new_token()
    now = timezone.now()
    invitation = OrganizationInvitation.objects.create(
        organization_id=organization_id,
        email=normalized_email,
        role=role,
        token_hash=_hash_token(token),
        expires_at=now + timedelta(days=INVITATION_TTL_DAYS),
        created_by=request.user,
        last_sent_at=now,
    )
    OrganizationInvitationStoreScope.objects.bulk_create([
        OrganizationInvitationStoreScope(
            organization_id=organization_id,
            invitation=invitation,
            store=store,
        )
        for store in stores
    ])
    _record_event(
        organization_id=organization_id,
        actor=request.user,
        event_type="invitation_created",
        invitation=invitation,
        payload={"email": normalized_email, "role": role, "storeIds": [str(value) for value in store_ids]},
    )
    role_name = invitation.get_role_display()
    store_names = [store.name for store in stores]
    transaction.on_commit(lambda: _send_invitation_email(
        email=normalized_email,
        organization_name=organization.name,
        role_name=role_name,
        store_names=store_names,
        token=token,
    ))
    return _invitation_repr(invitation)


@transaction.atomic
def resend_invitation(*, request, invitation_id):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    invitation = OrganizationInvitation.objects.select_for_update().filter(
        organization_id=organization_id,
        pk=invitation_id,
    ).select_related("organization").first()
    if invitation is None:
        raise AccessFlowError("resource_unavailable", "O convite não está disponível.", 404)
    if invitation.accepted_at or invitation.revoked_at:
        raise AccessFlowError("invitation_conflict", "O convite não pode ser reenviado neste estado.", 409)
    token = _new_token()
    now = timezone.now()
    invitation.token_hash = _hash_token(token)
    invitation.expires_at = now + timedelta(days=INVITATION_TTL_DAYS)
    invitation.sent_count += 1
    invitation.last_sent_at = now
    invitation.save(update_fields=["token_hash", "expires_at", "sent_count", "last_sent_at"])
    _record_event(
        organization_id=organization_id,
        actor=request.user,
        event_type="invitation_resent",
        invitation=invitation,
        payload={"expiresAt": invitation.expires_at.isoformat(), "sentCount": invitation.sent_count},
    )
    store_names = list(invitation.stores.order_by("name").values_list("name", flat=True))
    role_name = invitation.get_role_display()
    organization_name = invitation.organization.name
    email = invitation.email
    transaction.on_commit(lambda: _send_invitation_email(
        email=email,
        organization_name=organization_name,
        role_name=role_name,
        store_names=store_names,
        token=token,
    ))
    return _invitation_repr(invitation)


@transaction.atomic
def revoke_invitation(*, request, invitation_id):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    invitation = OrganizationInvitation.objects.select_for_update().filter(
        organization_id=organization_id,
        pk=invitation_id,
    ).first()
    if invitation is None:
        raise AccessFlowError("resource_unavailable", "O convite não está disponível.", 404)
    if invitation.accepted_at:
        raise AccessFlowError("invitation_conflict", "Um convite aceito não pode ser revogado.", 409)
    if invitation.revoked_at is None:
        invitation.revoked_by = request.user
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_by", "revoked_at"])
        _record_event(
            organization_id=organization_id,
            actor=request.user,
            event_type="invitation_revoked",
            invitation=invitation,
            payload={"email": invitation.email},
        )
    return _invitation_repr(invitation)


@transaction.atomic
def replace_membership_store_scope(*, request, membership_id, store_ids):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    membership = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        pk=membership_id,
        status=Membership.Status.ACTIVE,
    ).select_related("user").first()
    if membership is None:
        raise AccessFlowError("resource_unavailable", "A associação não está disponível.", 404)
    if membership.role == Membership.Role.ADMINISTRATOR and store_ids:
        raise AccessFlowError("membership_scope_invalid", "O administrador recebe escopo de toda a rede.")
    store_ids = list(dict.fromkeys(store_ids))
    if membership.role != Membership.Role.ADMINISTRATOR and not store_ids:
        raise AccessFlowError("membership_scope_required", "Selecione ao menos uma loja para este papel.")
    stores = list(Store.objects.filter(
        organization_id=organization_id,
        is_active=True,
        pk__in=store_ids,
    ))
    if len(stores) != len(store_ids):
        raise AccessFlowError("store_scope_invalid", "Uma ou mais lojas não estão disponíveis nesta rede.", 404)

    current = list(StoreAccessAssignment.objects.select_for_update().filter(
        organization_id=organization_id,
        membership=membership,
        revoked_at__isnull=True,
    ))
    current_by_store = {row.store_id: row for row in current}
    desired = set(store_ids)
    current_ids = set(current_by_store)
    now = timezone.now()
    for store_id in current_ids - desired:
        assignment = current_by_store[store_id]
        assignment.revoked_by = request.user
        assignment.revoked_at = now
        assignment.save(update_fields=["revoked_by", "revoked_at"])
    StoreAccessAssignment.objects.bulk_create([
        StoreAccessAssignment(
            organization_id=organization_id,
            membership=membership,
            store=store,
            assigned_by=request.user,
            assigned_at=now,
        )
        for store in stores
        if store.pk not in current_ids
    ])
    _record_event(
        organization_id=organization_id,
        actor=request.user,
        event_type="store_scope_changed",
        membership=membership,
        payload={
            "beforeStoreIds": [str(value) for value in sorted(current_ids, key=str)],
            "afterStoreIds": [str(value) for value in sorted(desired, key=str)],
        },
    )
    return _membership_repr(organization_id=organization_id, membership=membership)


@transaction.atomic
def update_membership_role(*, request, membership_id, role, store_ids):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    active_admins = list(Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        role=Membership.Role.ADMINISTRATOR,
        status=Membership.Status.ACTIVE,
    ).order_by("pk"))
    membership = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        pk=membership_id,
        status=Membership.Status.ACTIVE,
    ).select_related("user").first()
    if membership is None:
        raise AccessFlowError("resource_unavailable", "A associação não está disponível.", 404)
    if role not in Membership.Role.values:
        raise AccessFlowError("membership_role_invalid", "O papel informado não é válido.")

    store_ids = list(dict.fromkeys(store_ids))
    if role == Membership.Role.ADMINISTRATOR and store_ids:
        raise AccessFlowError("membership_scope_invalid", "O administrador recebe escopo de toda a rede.")
    if role != Membership.Role.ADMINISTRATOR and not store_ids:
        raise AccessFlowError("membership_scope_required", "Selecione ao menos uma loja para este papel.")
    if membership.role == Membership.Role.ADMINISTRATOR and role != Membership.Role.ADMINISTRATOR:
        if len(active_admins) <= 1:
            raise AccessFlowError(
                "last_administrator_required",
                "Promova outra pessoa antes de remover o último administrador.",
                409,
            )

    stores = list(Store.objects.filter(
        organization_id=organization_id,
        is_active=True,
        pk__in=store_ids,
    ))
    if len(stores) != len(store_ids):
        raise AccessFlowError("store_scope_invalid", "Uma ou mais lojas não estão disponíveis nesta rede.", 404)

    assignments = list(StoreAccessAssignment.objects.select_for_update().filter(
        organization_id=organization_id,
        membership=membership,
        revoked_at__isnull=True,
    ))
    before_store_ids = {row.store_id for row in assignments}
    desired_store_ids = set(store_ids)
    previous_role = membership.role
    now = timezone.now()
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])

    existing_by_store = {row.store_id: row for row in assignments}
    for store_id in before_store_ids - desired_store_ids:
        assignment = existing_by_store[store_id]
        assignment.revoked_by = request.user
        assignment.revoked_at = now
        assignment.save(update_fields=["revoked_by", "revoked_at"])
    StoreAccessAssignment.objects.bulk_create([
        StoreAccessAssignment(
            organization_id=organization_id,
            membership=membership,
            store=store,
            assigned_by=request.user,
            assigned_at=now,
        )
        for store in stores
        if store.pk not in before_store_ids
    ])
    _record_event(
        organization_id=organization_id,
        actor=request.user,
        event_type="membership_role_changed",
        membership=membership,
        payload={
            "beforeRole": previous_role,
            "afterRole": role,
            "beforeStoreIds": [str(value) for value in sorted(before_store_ids, key=str)],
            "afterStoreIds": [str(value) for value in sorted(desired_store_ids, key=str)],
        },
    )
    return _membership_repr(organization_id=organization_id, membership=membership)


@transaction.atomic
def preview_invitation(*, token):
    token_hash = _hash_token(token)
    set_invitation_token_context(token_hash=token_hash)
    invitation = OrganizationInvitation.objects.filter(token_hash=token_hash).select_related("organization").first()
    if invitation is None:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)
    if invitation.status != "pending" or not invitation.organization.is_active:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)
    scopes = invitation.store_scopes.select_related("store").order_by("store__name")
    return {
        "organizationName": invitation.organization.name,
        "email": invitation.email,
        "role": invitation.role,
        "roleName": invitation.get_role_display(),
        "storeNames": [row.store.name for row in scopes],
        "expiresAt": invitation.expires_at.isoformat(),
    }


def _new_pre_auth_challenge(*, user, purpose):
    return PreAuthChallenge.objects.create(
        user=user,
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=settings.PRE_AUTH_CHALLENGE_TTL_MINUTES),
    )


@transaction.atomic
def accept_invitation(*, request, token, full_name="", password=""):
    token_hash = _hash_token(token)
    set_invitation_token_context(token_hash=token_hash)
    invitation = OrganizationInvitation.objects.select_for_update(of=("self",)).filter(
        token_hash=token_hash,
    ).select_related("organization", "created_by").first()
    if invitation is None or invitation.status != "pending" or not invitation.organization.is_active:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)

    email = invitation.email
    existing_user = User.objects.filter(email=email).first()
    authenticated = bool(request.user and request.user.is_authenticated)
    if authenticated and request.user.email != email:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)
    if existing_user is not None and not authenticated:
        raise AccessFlowError(
            "invitation_login_required",
            "Entre com a conta convidada e envie novamente o token para aceitar.",
            401,
        )

    now = timezone.now()
    user = existing_user
    is_new_user = user is None
    if user is None:
        if len(full_name.strip()) < 2 or not password:
            raise AccessFlowError(
                "invitation_account_details_required",
                "Informe nome e senha para criar a conta convidada.",
            )
        user = User(email=email, full_name=full_name.strip())
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            messages = exc.messages
            raise AccessFlowError("invitation_password_invalid", " ".join(messages), 400) from exc
        user.set_password(password)
        user.email_verified_at = now
        user.is_active = True
        user.save(force_insert=True)
    elif not user.is_active or user.email_verified_at is None:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)

    organization_id = invitation.organization_id
    set_rls_context(organization_id=organization_id, user_id=user.pk)
    inviter_is_active_admin = Membership.objects.filter(
        organization_id=organization_id,
        user_id=invitation.created_by_id,
        status=Membership.Status.ACTIVE,
        role=Membership.Role.ADMINISTRATOR,
    ).exists()
    if not inviter_is_active_admin:
        raise AccessFlowError("invitation_invalid", "O convite é inválido ou expirou.", 404)
    membership = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        user_id=user.pk,
    ).first()
    if membership is not None and membership.status == Membership.Status.ACTIVE:
        raise AccessFlowError("membership_already_active", "A pessoa já tem acesso ativo a esta rede.", 409)

    scope_rows = list(invitation.store_scopes.select_related("store").all())
    if invitation.role == Membership.Role.ADMINISTRATOR and scope_rows:
        raise AccessFlowError("invitation_scope_invalid", "O administrador recebe escopo de toda a rede.", 409)
    if invitation.role != Membership.Role.ADMINISTRATOR and not scope_rows:
        raise AccessFlowError("invitation_scope_invalid", "O convite não tem lojas atribuídas.", 409)
    if any(
        row.organization_id != organization_id
        or not row.store.is_active
        or row.store.organization_id != organization_id
        for row in scope_rows
    ):
        raise AccessFlowError("invitation_scope_changed", "O escopo do convite mudou; solicite um novo convite.", 409)

    if membership is None:
        membership = Membership.objects.create(
            organization_id=organization_id,
            user=user,
            role=invitation.role,
            status=Membership.Status.ACTIVE,
        )
    else:
        membership.role = invitation.role
        membership.status = Membership.Status.ACTIVE
        membership.save(update_fields=["role", "status", "updated_at"])
        StoreAccessAssignment.objects.filter(
            organization_id=organization_id,
            membership=membership,
            revoked_at__isnull=True,
        ).update(revoked_by=invitation.created_by, revoked_at=now)
    StoreAccessAssignment.objects.bulk_create([
        StoreAccessAssignment(
            organization_id=organization_id,
            membership=membership,
            store=row.store,
            assigned_by=invitation.created_by,
            assigned_at=now,
        )
        for row in scope_rows
    ])
    invitation.accepted_by = user
    invitation.accepted_at = now
    invitation.save(update_fields=["accepted_by", "accepted_at"])
    _record_event(
        organization_id=organization_id,
        actor=user,
        event_type="invitation_accepted",
        invitation=invitation,
        payload={
            "membershipId": str(membership.pk),
            "role": membership.role,
            "storeIds": [str(row.store_id) for row in scope_rows],
        },
    )

    privileged_roles = {
        Membership.Role.ADMINISTRATOR,
        Membership.Role.STORE_MANAGER,
        Membership.Role.BUYER,
    }
    mfa_verified = request._request.session.get("mfa_verified") is True if authenticated else False
    if membership.role in privileged_roles and not mfa_verified:
        has_credentials = WebAuthnCredential.objects.filter(user=user, is_active=True).exists()
        has_recovery_codes = RecoveryCode.objects.filter(user=user, used_at__isnull=True).exists()
        purpose = (
            PreAuthChallenge.Purpose.AUTHENTICATION
            if has_credentials or has_recovery_codes
            else PreAuthChallenge.Purpose.ENROLLMENT
        )
        challenge = _new_pre_auth_challenge(user=user, purpose=purpose)
        if authenticated:
            from django.contrib.auth import logout

            logout(request._request)
        return {
            "status": "second_factor_required" if purpose == PreAuthChallenge.Purpose.AUTHENTICATION else "second_factor_enrollment_required",
            "challengeId": str(challenge.pk),
            "organizationId": str(organization_id),
            "methods": [
                method
                for available, method in (
                    (has_credentials, "webauthn"),
                    (has_recovery_codes, "recovery_code"),
                )
                if available
            ] or ["webauthn"],
        }

    if not authenticated:
        login(
            request._request,
            user,
            backend="django.contrib.auth.backends.ModelBackend",
        )
        request._request.session["mfa_verified"] = False
    select_active_organization(
        user=user,
        session=request._request.session,
        organization_id=organization_id,
    )
    return {
        "status": "accepted",
        "organizationId": str(organization_id),
        "membership": _membership_repr(organization_id=organization_id, membership=membership),
        "createdAccount": is_new_user,
    }


@transaction.atomic
def revoke_membership(*, request, membership_id):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    active_admins = list(Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        role=Membership.Role.ADMINISTRATOR,
        status=Membership.Status.ACTIVE,
    ).order_by("pk"))
    membership = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        pk=membership_id,
    ).select_related("user").first()
    if membership is None:
        raise AccessFlowError("resource_unavailable", "A associação não está disponível.", 404)
    if membership.status != Membership.Status.ACTIVE:
        return _membership_repr(organization_id=organization_id, membership=membership)
    if membership.role == Membership.Role.ADMINISTRATOR:
        if len(active_admins) <= 1:
            raise AccessFlowError("last_administrator_required", "Promova outra pessoa antes de remover o último administrador.", 409)
    membership.status = Membership.Status.REMOVED
    membership.save(update_fields=["status", "updated_at"])
    StoreAccessAssignment.objects.filter(
        organization_id=organization_id,
        membership=membership,
        revoked_at__isnull=True,
    ).update(revoked_by=request.user, revoked_at=timezone.now())
    _record_event(
        organization_id=organization_id,
        actor=request.user,
        event_type="membership_revoked",
        membership=membership,
        payload={"userId": str(membership.user_id), "role": membership.role},
    )
    return _membership_repr(organization_id=organization_id, membership=membership)
