"""Identity API endpoints."""

from django.contrib.auth import login, logout
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)
from rest_framework.views import APIView

from apps.identity.models import Membership, normalize_email_address
from apps.identity.permissions import MfaForPrivilegedUsers
from apps.identity.serializers import (
    EmailConfirmationSerializer,
    InvitationAcceptSerializer,
    InvitationCreateSerializer,
    InvitationTokenSerializer,
    LoginSerializer,
    MembershipRoleUpdateRequestSerializer,
    MembershipStoreScopeRequestSerializer,
    OrganizationSelectionSerializer,
    PreAuthChallengeSerializer,
    RecoveryCodeSerializer,
    RegistrationSerializer,
    ResendConfirmationSerializer,
    WebAuthnVerificationSerializer,
)
from apps.identity.services.authentication import InvalidCredentials, start_password_login
from apps.identity.services.mfa import (
    InvalidMfaChallenge,
    create_authentication_options,
    create_enrollment_options,
    verify_authentication,
    verify_enrollment,
    verify_recovery_code,
)
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    get_current_session_state,
    select_active_organization,
    select_sole_active_organization,
)
from apps.identity.services.invitations import (
    AccessFlowError,
    accept_invitation,
    create_invitation,
    list_invitations,
    list_memberships,
    preview_invitation,
    replace_membership_store_scope,
    resend_invitation,
    revoke_invitation,
    revoke_membership,
    update_membership_role,
)
from apps.identity.services.registration import (
    InvalidRegistrationToken,
    confirm_registration,
    resend_registration_confirmation,
    start_registration,
)
from apps.identity.throttling import IdentityScopedRateThrottle


def _validation_response(errors):
    return Response(
        {
            "code": "validation_error",
            "message": "Revise os dados informados.",
            "fields": errors,
        },
        status=400,
    )


def _accepted_response():
    return Response(
        {
            "status": "confirmation_required",
            "message": (
                "Se o endereço puder ser usado para iniciar o cadastro, "
                "enviaremos um link de confirmação."
            ),
        },
        status=202,
    )


def _invalid_login_response():
    return Response(
        {
            "code": "credentials_invalid",
            "message": "Não foi possível autenticar com os dados informados.",
        },
        status=HTTP_401_UNAUTHORIZED,
    )


def _invalid_mfa_response():
    return Response(
        {
            "code": "mfa_invalid",
            "message": "Não foi possível concluir a verificação de segurança.",
        },
        status=HTTP_401_UNAUTHORIZED,
    )


def _issue_session(request, user, *, mfa_verified=False):
    login(
        request._request,
        user,
        backend=getattr(user, "backend", "django.contrib.auth.backends.ModelBackend"),
    )
    request._request.session["mfa_verified"] = bool(mfa_verified)
    if mfa_verified:
        request._request.session["mfa_verified_at"] = timezone.now().isoformat()
    select_sole_active_organization(user=user, session=request._request.session)


class IdentityAPIView(APIView):
    def throttled(self, request, wait):
        raise Throttled(
            detail={
                "code": "rate_limited",
                "message": "Muitas tentativas. Aguarde antes de tentar novamente.",
            },
            wait=wait,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class RegistrationView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "registration"

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        values = serializer.validated_data
        start_registration(
            organization_name=values["organizationName"],
            administrator_name=values["administratorName"],
            email=values["email"],
            password=values["password"],
        )
        return _accepted_response()


@method_decorator(csrf_protect, name="dispatch")
class EmailConfirmationView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "email_confirmation"

    def post(self, request):
        serializer = EmailConfirmationSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            trial = confirm_registration(token=serializer.validated_data["token"])
        except InvalidRegistrationToken:
            return Response(
                {
                    "code": "confirmation_invalid",
                    "message": "O link de confirmação é inválido ou expirou.",
                },
                status=410,
            )

        return Response(
            {
                "status": "confirmed",
                "trialStartsAt": trial.trial_starts_at,
                "trialEndsAt": trial.trial_ends_at,
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class ResendEmailConfirmationView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "registration_resend"

    def post(self, request):
        serializer = ResendConfirmationSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        resend_registration_confirmation(
            email=normalize_email_address(serializer.validated_data["email"])
        )
        return Response({"status": "accepted"}, status=202)


@method_decorator(csrf_protect, name="dispatch")
class SessionView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        if getattr(request._request, "user", None) is not None and request._request.user.is_authenticated:
            return Response(
                {
                    "code": "session_already_active",
                    "message": "Encerre a sessão atual antes de entrar com outra conta.",
                },
                status=409,
            )

        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            state = start_password_login(
                request=request._request,
                email=normalize_email_address(serializer.validated_data["email"]),
                password=serializer.validated_data["password"],
            )
        except InvalidCredentials:
            return _invalid_login_response()

        if state.status == "authenticated":
            _issue_session(request, state.user)
            return Response({"status": "authenticated"})

        return Response(
            {
                "status": state.status,
                "challengeId": str(state.challenge_id),
                "methods": list(state.methods),
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class MfaEnrollmentOptionsView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "mfa"

    def post(self, request):
        serializer = PreAuthChallengeSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        try:
            options = create_enrollment_options(
                challenge_id=serializer.validated_data["challengeId"]
            )
        except InvalidMfaChallenge:
            return _invalid_mfa_response()
        return Response(options)


@method_decorator(csrf_protect, name="dispatch")
class MfaEnrollmentVerifyView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "mfa"

    def post(self, request):
        serializer = WebAuthnVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        result = verify_enrollment(
            challenge_id=serializer.validated_data["challengeId"],
            credential=serializer.validated_data["credential"],
        )
        if result is None:
            return _invalid_mfa_response()

        user, recovery_codes = result
        _issue_session(request, user, mfa_verified=True)
        return Response(
            {
                "status": "authenticated",
                "recoveryCodes": recovery_codes,
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class MfaAuthenticationOptionsView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "mfa"

    def post(self, request):
        serializer = PreAuthChallengeSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        try:
            options = create_authentication_options(
                challenge_id=serializer.validated_data["challengeId"]
            )
        except InvalidMfaChallenge:
            return _invalid_mfa_response()
        return Response(options)


@method_decorator(csrf_protect, name="dispatch")
class MfaAuthenticationVerifyView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "mfa"

    def post(self, request):
        serializer = WebAuthnVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        user = verify_authentication(
            challenge_id=serializer.validated_data["challengeId"],
            credential=serializer.validated_data["credential"],
        )
        if user is None:
            return _invalid_mfa_response()
        _issue_session(request, user, mfa_verified=True)
        return Response({"status": "authenticated"})


@method_decorator(csrf_protect, name="dispatch")
class MfaRecoveryCodeView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "mfa"

    def post(self, request):
        serializer = RecoveryCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        user = verify_recovery_code(
            challenge_id=serializer.validated_data["challengeId"],
            recovery_code=serializer.validated_data["recoveryCode"],
        )
        if user is None:
            return _invalid_mfa_response()
        _issue_session(request, user, mfa_verified=True)
        return Response({"status": "authenticated"})


@method_decorator(csrf_protect, name="dispatch")
class CurrentSessionView(IdentityAPIView):
    def get_permissions(self):
        if self.request.method == "DELETE":
            return [IsAuthenticated()]
        return [MfaForPrivilegedUsers()]

    def get(self, request):
        return Response(
            get_current_session_state(user=request.user, session=request._request.session)
        )

    def delete(self, request):
        logout(request._request)
        return Response(status=204)


@method_decorator(csrf_protect, name="dispatch")
class CurrentSessionOrganizationView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def put(self, request):
        serializer = OrganizationSelectionSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        organization_id = serializer.validated_data["organizationId"]
        if not select_active_organization(
            user=request.user,
            session=request._request.session,
            organization_id=organization_id,
        ):
            return Response(
                {
                    "code": "organization_unavailable",
                    "message": "A organização não está disponível para este usuário.",
                },
                status=HTTP_404_NOT_FOUND,
            )

        return Response(
            get_current_session_state(user=request.user, session=request._request.session)
        )


def _identity_admin_context(request):
    try:
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
    except ActiveOrganizationRequired:
        return None, Response(
            {"code": "active_organization_required", "message": "Selecione uma rede ativa."},
            status=HTTP_404_NOT_FOUND,
        )
    is_admin = Membership.objects.filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
        role=Membership.Role.ADMINISTRATOR,
    ).exists()
    if not is_admin:
        return None, Response(
            {"code": "administrator_required", "message": "Esta operação exige o administrador da rede."},
            status=HTTP_403_FORBIDDEN,
        )
    return organization_id, None


def _access_flow_error(exc):
    return Response(
        {"code": exc.code, "message": exc.message},
        status=exc.status,
    )


@method_decorator(csrf_protect, name="dispatch")
class CurrentOrganizationMembershipsView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def get(self, request):
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            return Response(list_memberships(request=request), status=HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class CurrentOrganizationInvitationsView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def get(self, request):
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            return Response(list_invitations(request=request), status=HTTP_200_OK)

    def post(self, request):
        serializer = InvitationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                invitation = create_invitation(
                    request=request,
                    email=serializer.validated_data["email"],
                    role=serializer.validated_data["role"],
                    store_ids=serializer.validated_data["storeIds"],
                )
            except AccessFlowError as exc:
                return _access_flow_error(exc)
            except IntegrityError:
                return Response(
                    {"code": "invitation_conflict", "message": "O convite mudou durante a operação."},
                    status=HTTP_409_CONFLICT,
                )
            return Response(invitation, status=HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class InvitationResendView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def post(self, request, invitation_id):
        if request.data not in ({}, None):
            return _validation_response({"non_field_errors": ["O reenvio não recebe campos."]})
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                return Response(resend_invitation(request=request, invitation_id=invitation_id))
            except AccessFlowError as exc:
                return _access_flow_error(exc)


@method_decorator(csrf_protect, name="dispatch")
class InvitationRevokeView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def post(self, request, invitation_id):
        if request.data not in ({}, None):
            return _validation_response({"non_field_errors": ["A revogação não recebe campos."]})
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                return Response(revoke_invitation(request=request, invitation_id=invitation_id))
            except AccessFlowError as exc:
                return _access_flow_error(exc)


@method_decorator(csrf_protect, name="dispatch")
class MembershipStoreScopeView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def put(self, request, membership_id):
        serializer = MembershipStoreScopeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                membership = replace_membership_store_scope(
                    request=request,
                    membership_id=membership_id,
                    store_ids=serializer.validated_data["storeIds"],
                )
            except AccessFlowError as exc:
                return _access_flow_error(exc)
            return Response(membership, status=HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class MembershipRoleView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def put(self, request, membership_id):
        serializer = MembershipRoleUpdateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                membership = update_membership_role(
                    request=request,
                    membership_id=membership_id,
                    role=serializer.validated_data["role"],
                    store_ids=serializer.validated_data["storeIds"],
                )
            except AccessFlowError as exc:
                return _access_flow_error(exc)
            except IntegrityError:
                return Response(
                    {"code": "membership_role_conflict", "message": "A associação mudou durante a operação."},
                    status=HTTP_409_CONFLICT,
                )
            return Response(membership, status=HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class MembershipRevokeView(IdentityAPIView):
    permission_classes = [MfaForPrivilegedUsers]

    def post(self, request, membership_id):
        if request.data not in ({}, None):
            return _validation_response({"non_field_errors": ["A remoção não recebe campos."]})
        with transaction.atomic():
            _, error = _identity_admin_context(request)
            if error:
                return error
            try:
                membership = revoke_membership(request=request, membership_id=membership_id)
            except AccessFlowError as exc:
                return _access_flow_error(exc)
            return Response(membership, status=HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class InvitationPreviewView(IdentityAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "invitation"

    def post(self, request):
        serializer = InvitationTokenSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        try:
            with transaction.atomic():
                return Response(
                    preview_invitation(token=serializer.validated_data["token"]),
                    status=HTTP_200_OK,
                )
        except AccessFlowError as exc:
            return _access_flow_error(exc)


@method_decorator(csrf_protect, name="dispatch")
class InvitationAcceptView(IdentityAPIView):
    permission_classes = [AllowAny]
    throttle_classes = [IdentityScopedRateThrottle]
    throttle_scope = "invitation"

    def post(self, request):
        serializer = InvitationAcceptSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        try:
            with transaction.atomic():
                result = accept_invitation(
                    request=request,
                    token=serializer.validated_data["token"],
                    full_name=serializer.validated_data.get("fullName", ""),
                    password=serializer.validated_data.get("password", ""),
                )
        except AccessFlowError as exc:
            return _access_flow_error(exc)
        except IntegrityError:
            return Response(
                {"code": "invitation_conflict", "message": "O convite já foi aceito ou não pode ser aplicado."},
                status=HTTP_409_CONFLICT,
            )
        return Response(result, status=HTTP_200_OK)
