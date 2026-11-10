"""Strict request schemas for replenishment policies and data quality signals."""

from collections.abc import Mapping

from rest_framework import serializers

from apps.inventory.models import ProductCategory
from apps.inventory.serializers import ExactQuantityStringField


class StrictInputMixin:
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {"non_field_errors": [f"Campos não aceitos: {', '.join(sorted(unknown))}."]}
                )
        return super().to_internal_value(data)


class ProductCategoryCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=120, trim_whitespace=True)

    def validate_name(self, value):
        if not value:
            raise serializers.ValidationError("Informe o nome da categoria.")
        return value


class ProductCategorySerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = ProductCategory
        fields = ["id", "name", "createdAt"]


class ReplenishmentPolicyPatchSerializer(StrictInputMixin, serializers.Serializer):
    minimumStock = ExactQuantityStringField(
        max_digits=14,
        decimal_places=3,
        min_value=0,
        required=False,
        allow_null=True,
    )
    safetyDays = serializers.IntegerField(min_value=0, max_value=365, required=False, allow_null=True)
    reviewIntervalDays = serializers.IntegerField(
        min_value=1,
        max_value=365,
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Informe ao menos um parâmetro para alterar.")
        return attrs


class SalesCoverageCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    operationalDateFrom = serializers.DateField()
    operationalDateTo = serializers.DateField()

    def validate(self, attrs):
        if attrs["operationalDateTo"] < attrs["operationalDateFrom"]:
            raise serializers.ValidationError(
                {"operationalDateTo": ["A data final deve ser igual ou posterior à inicial."]}
            )
        return attrs


class OutOfStockDayCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField()
    operationalDate = serializers.DateField()


class SalesCoverageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    operationalDateFrom = serializers.DateField(source="operational_date_from")
    operationalDateTo = serializers.DateField(source="operational_date_to")
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")


class OutOfStockDaySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    productId = serializers.UUIDField(source="product_id")
    operationalDate = serializers.DateField(source="operational_date")
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")
