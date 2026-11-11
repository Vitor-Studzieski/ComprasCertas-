"""Global identity and tenant membership models for CompraCerta."""

import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def normalize_email_address(email: str) -> str:
    """Normalize email identity for case-insensitive account lookup."""
    if not email:
        raise ValueError("An email address is required.")
    return BaseUserManager.normalize_email(email.strip()).casefold()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields):
        email = normalize_email_address(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, email: str):
        return self.get(email=normalize_email_address(email))


class User(AbstractBaseUser, PermissionsMixin):
    """A person account that can hold memberships in multiple organizations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    full_name = models.CharField(max_length=160)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "identity_user"
        ordering = ["email"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="identity_user_email_ci_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email


class Organization(models.Model):
    """A customer network and the tenant boundary for its business data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_organization"
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    """A user's role in one organization; store scope is assigned separately."""

    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Administrador da rede"
        STORE_MANAGER = "store_manager", "Gerente de loja"
        OPERATOR = "operator", "Operador"
        BUYER = "buyer", "Comprador/aprovador"

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativo"
        SUSPENDED = "suspended", "Suspenso"
        REMOVED = "removed", "Removido"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    role = models.CharField(max_length=24, choices=Role.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_membership"
        ordering = ["organization_id", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="identity_membership_org_user_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="identity_mem_user_status_idx"),
            models.Index(fields=["organization", "role", "status"], name="identity_mem_org_role_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.organization_id}:{self.role}"


class StoreAccessAssignment(models.Model):
    """Auditable access for a non-administrator membership to one store."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="store_access_assignments",
    )
    membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="store_assignments",
    )
    store = models.ForeignKey(
        "inventory.Store",
        on_delete=models.PROTECT,
        related_name="access_assignments",
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="store_access_assignments_created",
    )
    assigned_at = models.DateTimeField(default=timezone.now, editable=False)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="store_access_assignments_revoked",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_store_access_assignment"
        ordering = ["organization_id", "membership_id", "store_id", "assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "membership", "store"],
                condition=models.Q(revoked_at__isnull=True),
                name="identity_store_access_active_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(revoked_at__isnull=True, revoked_by__isnull=True)
                    | models.Q(revoked_at__isnull=False, revoked_by__isnull=False)
                ),
                name="identity_store_access_revocation_actor_valid",
            ),
        ]


class OrganizationInvitation(models.Model):
    """Single-use email invitation to join one tenant with a role and store scope."""

    class Role(models.TextChoices):
        ADMINISTRATOR = Membership.Role.ADMINISTRATOR, "Administrador da rede"
        STORE_MANAGER = Membership.Role.STORE_MANAGER, "Gerente de loja"
        OPERATOR = Membership.Role.OPERATOR, "Operador"
        BUYER = Membership.Role.BUYER, "Comprador/aprovador"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="invitations",
    )
    email = models.EmailField(max_length=254)
    role = models.CharField(max_length=24, choices=Role.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="organization_invitations_created",
    )
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="organization_invitations_accepted",
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="organization_invitations_revoked",
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveSmallIntegerField(default=1)
    last_sent_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    stores = models.ManyToManyField(
        "inventory.Store",
        through="OrganizationInvitationStoreScope",
        related_name="organization_invitations",
    )

    class Meta:
        db_table = "identity_organization_invitation"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["organization", "email"], name="identity_inv_org_email_idx"),
            models.Index(fields=["expires_at"], name="identity_inv_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(accepted_at__isnull=True, accepted_by__isnull=True)
                    | models.Q(accepted_at__isnull=False, accepted_by__isnull=False)
                ),
                name="identity_inv_accept_actor_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(revoked_at__isnull=True, revoked_by__isnull=True)
                    | models.Q(revoked_at__isnull=False, revoked_by__isnull=False)
                ),
                name="identity_inv_revoke_actor_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        return super().save(*args, **kwargs)

    @property
    def status(self) -> str:
        if self.accepted_at is not None:
            return "accepted"
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at <= timezone.now():
            return "expired"
        return "pending"


class OrganizationInvitationStoreScope(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="invitation_store_scopes",
    )
    invitation = models.ForeignKey(
        OrganizationInvitation,
        on_delete=models.PROTECT,
        related_name="store_scopes",
    )
    store = models.ForeignKey(
        "inventory.Store",
        on_delete=models.PROTECT,
        related_name="invitation_scopes",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "identity_invitation_store_scope"
        ordering = ["invitation_id", "store_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "invitation", "store"],
                name="identity_inv_scope_unique",
            ),
        ]


class MembershipAccessEvent(models.Model):
    """Immutable audit of access changes and invitation lifecycle events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="membership_access_events",
    )
    membership = models.ForeignKey(
        Membership,
        on_delete=models.PROTECT,
        related_name="access_events",
        null=True,
        blank=True,
    )
    invitation = models.ForeignKey(
        OrganizationInvitation,
        on_delete=models.PROTECT,
        related_name="access_events",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="membership_access_events_created",
    )
    event_type = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "identity_membership_access_event"
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(membership__isnull=False, invitation__isnull=True)
                    | models.Q(membership__isnull=True, invitation__isnull=False)
                ),
                name="identity_access_event_one_subject",
            ),
        ]


class Trial(models.Model):
    """The one-time 30-day product trial created at email confirmation."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativo"
        CONVERTED = "converted", "Convertido"
        EXPIRED = "expired", "Expirado"
        CANCELLED = "cancelled", "Cancelado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization,
        on_delete=models.PROTECT,
        related_name="trial",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_trial"
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="identity_trial_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.status}"


class PendingRegistration(models.Model):
    """Unconfirmed signup data; the token and password are stored only hashed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_name = models.CharField(max_length=160)
    administrator_name = models.CharField(max_length=160)
    email = models.EmailField(max_length=254, unique=True)
    password_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "identity_pending_registration"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="identity_pending_email_ci_unique",
            ),
        ]
        indexes = [models.Index(fields=["expires_at"], name="identity_pending_expires_idx")]

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.email


class PendingRegistrationToken(models.Model):
    """One issued confirmation token; resends do not invalidate older links."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration = models.ForeignKey(
        PendingRegistration,
        on_delete=models.CASCADE,
        related_name="confirmation_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "identity_pending_registration_token"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["expires_at"], name="identity_token_expires_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.registration_id}:{self.expires_at.isoformat()}"


class PreAuthChallenge(models.Model):
    """Short-lived password-authenticated state; never an app session."""

    class Purpose(models.TextChoices):
        ENROLLMENT = "enrollment", "Cadastro de fator"
        AUTHENTICATION = "authentication", "Verificação de fator"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pre_auth_challenges",
    )
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    webauthn_challenge = models.BinaryField(default=bytes, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "identity_preauth_challenge"
        indexes = [
            models.Index(fields=["user", "purpose"], name="identity_preauth_user_idx"),
            models.Index(fields=["expires_at"], name="identity_preauth_expire_idx"),
        ]


class WebAuthnCredential(models.Model):
    """Public credential material used to verify a user's passkey/security key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="webauthn_credentials",
    )
    credential_id = models.BinaryField(unique=True)
    credential_public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    device_type = models.CharField(max_length=32, blank=True)
    credential_backed_up = models.BooleanField(default=False)
    label = models.CharField(max_length=100, default="Minha passkey")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_webauthn_credential"
        indexes = [
            models.Index(fields=["user", "is_active"], name="identity_webauth_user_idx"),
        ]


class RecoveryCode(models.Model):
    """One-time backup code; only a password hash is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_recovery_code"
        indexes = [
            models.Index(fields=["user", "used_at"], name="identity_recovery_user_idx"),
        ]
