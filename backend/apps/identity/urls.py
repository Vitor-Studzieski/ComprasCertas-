"""URL routes for account registration, login, and MFA."""

from django.urls import path

from apps.identity.views import (
    CsrfTokenView,
    CurrentSessionOrganizationView,
    CurrentSessionView,
    EmailConfirmationView,
    MfaAuthenticationOptionsView,
    MfaAuthenticationVerifyView,
    MfaEnrollmentOptionsView,
    MfaEnrollmentVerifyView,
    MfaRecoveryCodeView,
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
]
