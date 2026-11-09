"""WebAuthn ceremonies and one-time recovery code handling."""

import json
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from apps.identity.models import PreAuthChallenge, RecoveryCode, WebAuthnCredential


class InvalidMfaChallenge(Exception):
    """Challenge is missing, expired, consumed, or belongs to another ceremony."""


def _locked_challenge(challenge_id: UUID, purpose: str) -> PreAuthChallenge | None:
    now = timezone.now()
    challenge = (
        PreAuthChallenge.objects.select_for_update()
        .filter(pk=challenge_id, purpose=purpose)
        .first()
    )
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
        or challenge.attempts >= settings.MFA_CHALLENGE_MAX_ATTEMPTS
    ):
        return None
    return challenge


def _record_failed_attempt(challenge: PreAuthChallenge) -> None:
    challenge.attempts += 1
    if challenge.attempts >= settings.MFA_CHALLENGE_MAX_ATTEMPTS:
        challenge.consumed_at = timezone.now()
    challenge.save(update_fields=["attempts", "consumed_at"])


def _serialize_options(options) -> dict:
    return json.loads(options_to_json(options))


def create_enrollment_options(*, challenge_id: UUID) -> dict:
    with transaction.atomic():
        challenge = _locked_challenge(challenge_id, PreAuthChallenge.Purpose.ENROLLMENT)
        if challenge is None:
            raise InvalidMfaChallenge
        if WebAuthnCredential.objects.filter(user=challenge.user, is_active=True).exists():
            raise InvalidMfaChallenge

        options = generate_registration_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            rp_name=settings.WEBAUTHN_RP_NAME,
            user_name=challenge.user.email,
            user_display_name=challenge.user.full_name,
            user_id=challenge.user.id.bytes,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        challenge.webauthn_challenge = options.challenge
        challenge.save(update_fields=["webauthn_challenge"])
        return _serialize_options(options)


def create_authentication_options(*, challenge_id: UUID) -> dict:
    with transaction.atomic():
        challenge = _locked_challenge(challenge_id, PreAuthChallenge.Purpose.AUTHENTICATION)
        if challenge is None:
            raise InvalidMfaChallenge

        credentials = list(
            WebAuthnCredential.objects.filter(user=challenge.user, is_active=True)
        )
        if not credentials:
            raise InvalidMfaChallenge

        descriptors = []
        for credential in credentials:
            transports = []
            for transport in credential.transports:
                try:
                    transports.append(AuthenticatorTransport(transport))
                except ValueError:
                    continue
            descriptors.append(
                PublicKeyCredentialDescriptor(
                    id=bytes(credential.credential_id),
                    transports=transports or None,
                )
            )

        options = generate_authentication_options(
            rp_id=settings.WEBAUTHN_RP_ID,
            allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        challenge.webauthn_challenge = options.challenge
        challenge.save(update_fields=["webauthn_challenge"])
        return _serialize_options(options)


def verify_enrollment(*, challenge_id: UUID, credential: dict):
    """Verify a new credential, store its public data, and issue recovery codes."""
    with transaction.atomic():
        challenge = _locked_challenge(challenge_id, PreAuthChallenge.Purpose.ENROLLMENT)
        if challenge is None or not challenge.webauthn_challenge:
            return None
        if WebAuthnCredential.objects.filter(user=challenge.user, is_active=True).exists():
            return None

        try:
            verification = verify_registration_response(
                credential=credential,
                expected_challenge=bytes(challenge.webauthn_challenge),
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                expected_origin=settings.WEBAUTHN_ORIGINS,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError):
            _record_failed_attempt(challenge)
            return None

        raw_transports = credential.get("response", {}).get("transports", [])
        transports = (
            [value for value in raw_transports if isinstance(value, str)]
            if isinstance(raw_transports, list)
            else []
        )
        try:
            with transaction.atomic():
                WebAuthnCredential.objects.create(
                    user=challenge.user,
                    credential_id=verification.credential_id,
                    credential_public_key=verification.credential_public_key,
                    sign_count=verification.sign_count,
                    transports=transports,
                    device_type=verification.credential_device_type.value,
                    credential_backed_up=verification.credential_backed_up,
                )
        except IntegrityError:
            _record_failed_attempt(challenge)
            return None

        recovery_codes = []
        for _ in range(settings.RECOVERY_CODES_PER_USER):
            code = secrets.token_hex(8).upper()
            RecoveryCode.objects.create(user=challenge.user, code_hash=make_password(code))
            recovery_codes.append(code)

        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
        return challenge.user, recovery_codes


def verify_authentication(*, challenge_id: UUID, credential: dict):
    """Verify a WebAuthn assertion and return its user on success."""
    with transaction.atomic():
        challenge = _locked_challenge(challenge_id, PreAuthChallenge.Purpose.AUTHENTICATION)
        if challenge is None or not challenge.webauthn_challenge:
            return None

        encoded_id = credential.get("rawId") or credential.get("id")
        try:
            credential_id = base64url_to_bytes(encoded_id)
        except (TypeError, ValueError):
            _record_failed_attempt(challenge)
            return None

        stored_credential = (
            WebAuthnCredential.objects.select_for_update()
            .filter(user=challenge.user, credential_id=credential_id, is_active=True)
            .first()
        )
        if stored_credential is None:
            _record_failed_attempt(challenge)
            return None

        try:
            verification = verify_authentication_response(
                credential=credential,
                expected_challenge=bytes(challenge.webauthn_challenge),
                expected_rp_id=settings.WEBAUTHN_RP_ID,
                expected_origin=settings.WEBAUTHN_ORIGINS,
                credential_public_key=bytes(stored_credential.credential_public_key),
                credential_current_sign_count=stored_credential.sign_count,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError):
            _record_failed_attempt(challenge)
            return None

        now = timezone.now()
        stored_credential.sign_count = verification.new_sign_count
        stored_credential.device_type = verification.credential_device_type.value
        stored_credential.credential_backed_up = verification.credential_backed_up
        stored_credential.last_used_at = now
        stored_credential.save(
            update_fields=[
                "sign_count",
                "device_type",
                "credential_backed_up",
                "last_used_at",
            ]
        )
        challenge.consumed_at = now
        challenge.save(update_fields=["consumed_at"])
        return challenge.user


def verify_recovery_code(*, challenge_id: UUID, recovery_code: str):
    """Consume a matching recovery code atomically for an MFA login."""
    normalized_code = recovery_code.strip().upper().replace("-", "")
    with transaction.atomic():
        challenge = _locked_challenge(challenge_id, PreAuthChallenge.Purpose.AUTHENTICATION)
        if challenge is None:
            return None

        codes = RecoveryCode.objects.select_for_update().filter(
            user=challenge.user,
            used_at__isnull=True,
        )
        matched = next((code for code in codes if check_password(normalized_code, code.code_hash)), None)
        if matched is None:
            _record_failed_attempt(challenge)
            return None

        now = timezone.now()
        matched.used_at = now
        matched.save(update_fields=["used_at"])
        challenge.consumed_at = now
        challenge.save(update_fields=["consumed_at"])
        return challenge.user
