"""URL routes for account registration, login, and MFA."""

from django.urls import path

from apps.identity.views import (
    CsrfTokenView,
    CurrentOrganizationInvitationsView,
    CurrentOrganizationMembershipsView,
    CurrentSessionOrganizationView,
    CurrentSessionView,
    EmailConfirmationView,
    InvitationAcceptView,
    InvitationPreviewView,
    InvitationResendView,
    InvitationRevokeView,
    MfaAuthenticationOptionsView,
    MfaAuthenticationVerifyView,
    MfaEnrollmentOptionsView,
    MfaEnrollmentVerifyView,
    MfaRecoveryCodeView,
    MembershipRevokeView,
    MembershipRoleView,
    MembershipStoreScopeView,
    RegistrationView,
    ResendEmailConfirmationView,
    SessionView,
)

urlpatterns = [
    path("csrf/", CsrfTokenView.as_view(), name="csrf-token"),
    path("registrations/", RegistrationView.as_view(), name="registration-create"),
    path("email-confirmations/", EmailConfirmationView.as_view(), name="email-confirmation"),
    path(
        "email-confirmations/resend/",
        ResendEmailConfirmationView.as_view(),
        name="email-confirmation-resend",
    ),
    path("sessions/", SessionView.as_view(), name="session-create"),
    path(
        "sessions/mfa/enrollment/options/",
        MfaEnrollmentOptionsView.as_view(),
        name="mfa-enrollment-options",
    ),
    path(
        "sessions/mfa/enrollment/verify/",
        MfaEnrollmentVerifyView.as_view(),
        name="mfa-enrollment-verify",
    ),
    path(
        "sessions/mfa/authentication/options/",
        MfaAuthenticationOptionsView.as_view(),
        name="mfa-authentication-options",
    ),
    path(
        "sessions/mfa/authentication/verify/",
        MfaAuthenticationVerifyView.as_view(),
        name="mfa-authentication-verify",
    ),
    path("sessions/mfa/recovery-code/", MfaRecoveryCodeView.as_view(), name="mfa-recovery-code"),
    path("sessions/current/", CurrentSessionView.as_view(), name="session-current"),
    path(
        "sessions/current/organization/",
        CurrentSessionOrganizationView.as_view(),
        name="session-current-organization",
    ),
    path("organizations/current/memberships/", CurrentOrganizationMembershipsView.as_view(), name="membership-list"),
    path(
        "organizations/current/memberships/<uuid:membership_id>/store-scope/",
        MembershipStoreScopeView.as_view(),
        name="membership-store-scope",
    ),
    path(
        "organizations/current/memberships/<uuid:membership_id>/role/",
        MembershipRoleView.as_view(),
        name="membership-role-update",
    ),
    path(
        "organizations/current/memberships/<uuid:membership_id>/revoke/",
        MembershipRevokeView.as_view(),
        name="membership-revoke",
    ),
    path("organizations/current/invitations/", CurrentOrganizationInvitationsView.as_view(), name="invitation-list-create"),
    path(
        "organizations/current/invitations/<uuid:invitation_id>/resend/",
        InvitationResendView.as_view(),
        name="invitation-resend",
    ),
    path(
        "organizations/current/invitations/<uuid:invitation_id>/revoke/",
        InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path("invitations/preview/", InvitationPreviewView.as_view(), name="invitation-preview"),
    path("invitations/accept/", InvitationAcceptView.as_view(), name="invitation-accept"),
]
