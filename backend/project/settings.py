"""Settings for the CompraCerta API project."""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY or SECRET_KEY == "replace-with-a-secret-key":
    if DEBUG:
        SECRET_KEY = secrets.token_urlsafe(64)
    else:
        raise ImproperlyConfigured("A real DJANGO_SECRET_KEY must be configured when DEBUG is false.")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1",
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core.apps.CoreConfig",
    "apps.identity.apps.IdentityConfig",
    "apps.inventory.apps.InventoryConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.purchasing.apps.PurchasingConfig",
    "apps.imports.apps.ImportsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"
ASGI_APPLICATION = "project.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "comprascerta"),
        "USER": os.environ.get("POSTGRES_APP_USER", "comprascerta_app"),
        "PASSWORD": os.environ.get("POSTGRES_APP_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "identity.User"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.identity.permissions.MfaForPrivilegedUsers"],
    "DEFAULT_THROTTLE_RATES": {
        "registration": "5/hour",
        "registration_resend": "3/hour",
        "email_confirmation": "10/hour",
        "invitation": "10/hour",
        "login": "10/minute",
        "mfa": "20/minute",
    },
}

EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "CompraCerta <no-reply@localhost>" if DEBUG else "",
)
FRONTEND_BASE_URL = os.environ.get(
    "FRONTEND_BASE_URL",
    "http://localhost:5173" if DEBUG else "",
).rstrip("/")
REGISTRATION_TOKEN_TTL_HOURS = int(os.environ.get("REGISTRATION_TOKEN_TTL_HOURS", "24"))
if REGISTRATION_TOKEN_TTL_HOURS < 1:
    raise ImproperlyConfigured("REGISTRATION_TOKEN_TTL_HOURS must be a positive integer.")

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True

if not DEBUG and not DEFAULT_FROM_EMAIL:
    raise ImproperlyConfigured("DEFAULT_FROM_EMAIL must be configured when DEBUG is false.")
if not DEBUG and not FRONTEND_BASE_URL:
    raise ImproperlyConfigured("FRONTEND_BASE_URL must be configured when DEBUG is false.")
if (
    not DEBUG
    and EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
    and not EMAIL_HOST
):
    raise ImproperlyConfigured("EMAIL_HOST must be configured for the SMTP backend.")

PRE_AUTH_CHALLENGE_TTL_MINUTES = int(os.environ.get("PRE_AUTH_CHALLENGE_TTL_MINUTES", "10"))
MFA_CHALLENGE_MAX_ATTEMPTS = int(os.environ.get("MFA_CHALLENGE_MAX_ATTEMPTS", "5"))
RECOVERY_CODES_PER_USER = int(os.environ.get("RECOVERY_CODES_PER_USER", "10"))
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "CompraCerta")
WEBAUTHN_RP_ID = os.environ.get("WEBAUTHN_RP_ID", "localhost" if DEBUG else "")
WEBAUTHN_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "WEBAUTHN_ORIGINS",
        "http://localhost:5173" if DEBUG else "",
    ).split(",")
    if origin.strip()
]
if PRE_AUTH_CHALLENGE_TTL_MINUTES < 1 or MFA_CHALLENGE_MAX_ATTEMPTS < 1:
    raise ImproperlyConfigured("MFA challenge TTL and attempt limit must be positive integers.")
if RECOVERY_CODES_PER_USER < 1:
    raise ImproperlyConfigured("RECOVERY_CODES_PER_USER must be a positive integer.")
if not DEBUG and (not WEBAUTHN_RP_ID or not WEBAUTHN_ORIGINS):
    raise ImproperlyConfigured("WEBAUTHN_RP_ID and WEBAUTHN_ORIGINS must be configured when DEBUG is false.")
