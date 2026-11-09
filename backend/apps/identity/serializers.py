"""Request serializers for public identity endpoints."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.identity.models import User, normalize_email_address


class RegistrationSerializer(serializers.Serializer):
    organizationName = serializers.CharField(min_length=2, max_length=160, trim_whitespace=True)
    administratorName = serializers.CharField(min_length=2, max_length=160, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(min_length=1, max_length=256, write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = User(
            email=normalize_email_address(attrs["email"]),
            full_name=attrs["administratorName"],
        )
        try:
            validate_password(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs


class EmailConfirmationSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=1, max_length=128, trim_whitespace=True)


class ResendConfirmationSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(min_length=1, max_length=256, write_only=True, trim_whitespace=False)


class OrganizationSelectionSerializer(serializers.Serializer):
    organizationId = serializers.UUIDField()


class PreAuthChallengeSerializer(serializers.Serializer):
    challengeId = serializers.UUIDField()


class WebAuthnVerificationSerializer(PreAuthChallengeSerializer):
    credential = serializers.JSONField()

    def validate_credential(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("response"), dict):
            raise serializers.ValidationError("A resposta do autenticador está malformada.")
        return value


class RecoveryCodeSerializer(PreAuthChallengeSerializer):
    recoveryCode = serializers.CharField(min_length=1, max_length=64, write_only=True, trim_whitespace=True)
