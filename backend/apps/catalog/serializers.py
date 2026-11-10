"""Strict API representations for suppliers and supplier-product conditions."""

import re
from collections.abc import Mapping
from decimal import Decimal

from rest_framework import serializers

from apps.catalog.models import Supplier, SupplierProductCondition


class StrictInputMixin:
    """Reject undeclared fields instead of silently ignoring them."""

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {"non_field_errors": [f"Campos não aceitos: {', '.join(sorted(unknown))}."]}
                )
        return super().to_internal_value(data)


class ExactDecimalStringField(serializers.DecimalField):
    """Accept exact decimal strings only, with the model field's scale."""

    def __init__(self, *args, **kwargs):
        decimal_places = kwargs.get("decimal_places", 3)
        self.pattern = re.compile(
            rf"^(0|[1-9][0-9]*)(\.[0-9]{{1,{decimal_places}}})?$"
        )
        super().__init__(*args, **kwargs)

    default_error_messages = {
        **serializers.DecimalField.default_error_messages,
        "invalid": "Informe a quantidade como uma string decimal exata.",
    }

    def to_internal_value(self, data):
        if not isinstance(data, str) or not self.pattern.fullmatch(data):
            self.fail("invalid")
        return super().to_internal_value(data)


class SupplierCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=160, trim_whitespace=True)
    contactName = serializers.CharField(
        source="contact_name", max_length=160, required=False, allow_blank=True, trim_whitespace=True
    )
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, trim_whitespace=True)
    notes = serializers.CharField(max_length=1000, required=False, allow_blank=True, trim_whitespace=True)

    def validate_name(self, value):
        if not value:
            raise serializers.ValidationError("Informe o nome do fornecedor.")
        return value


class SupplierUpdateRequestSerializer(SupplierCreateRequestSerializer):
    isActive = serializers.BooleanField(source="is_active", required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError({"non_field_errors": ["Informe ao menos um campo."]})
        return attrs


class SupplierSerializer(serializers.ModelSerializer):
    contactName = serializers.CharField(source="contact_name")
    isActive = serializers.BooleanField(source="is_active")
    createdBy = serializers.UUIDField(source="created_by_id")
    updatedBy = serializers.UUIDField(source="updated_by_id")
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "contactName",
            "email",
            "phone",
            "notes",
            "isActive",
            "createdBy",
            "updatedBy",
            "createdAt",
            "updatedAt",
        ]


class SupplierListSerializer(serializers.Serializer):
    suppliers = SupplierSerializer(many=True)


class SupplierProductConditionQuerySerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField(source="product_id", required=False)
    storeId = serializers.UUIDField(source="store_id", required=False)


class SupplierProductConditionCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField(source="product_id")
    storeId = serializers.UUIDField(source="store_id", required=False, allow_null=True)
    purchaseUnitName = serializers.CharField(
        source="purchase_unit_name",
        min_length=1,
        max_length=40,
        trim_whitespace=True,
    )
    baseUnitsPerPurchaseUnit = ExactDecimalStringField(
        source="base_units_per_purchase_unit",
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        help_text="Unidades-base de estoque contidas em uma unidade de compra.",
    )
    pricePerPurchaseUnit = ExactDecimalStringField(
        source="price_per_purchase_unit",
        max_digits=14,
        decimal_places=2,
        min_value=0,
        help_text="Preço em reais por unidade de compra.",
    )
    deliveryLeadTimeDays = serializers.IntegerField(
        source="delivery_lead_time_days",
        min_value=0,
        max_value=32767,
        help_text="Prazo de entrega contado em dias corridos.",
    )
    minimumPurchaseQuantity = ExactDecimalStringField(
        source="minimum_purchase_quantity",
        max_digits=14,
        decimal_places=3,
        min_value=0,
        help_text="Pedido mínimo expresso na unidade de compra.",
    )
    purchaseMultiple = ExactDecimalStringField(
        source="purchase_multiple",
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        help_text="Múltiplo de compra expresso na unidade de compra.",
    )
    isAvailable = serializers.BooleanField(source="is_available")
    effectiveFrom = serializers.DateField(source="effective_from")


class SupplierProductConditionSerializer(serializers.ModelSerializer):
    supplierId = serializers.UUIDField(source="supplier_id")
    productId = serializers.UUIDField(source="product_id")
    storeId = serializers.UUIDField(source="store_id", allow_null=True)
    effectiveFrom = serializers.DateField(source="effective_from")
    purchaseUnitName = serializers.CharField(source="purchase_unit_name")
    baseUnitsPerPurchaseUnit = serializers.DecimalField(
        source="base_units_per_purchase_unit",
        max_digits=14,
        decimal_places=3,
        coerce_to_string=True,
    )
    pricePerPurchaseUnit = serializers.DecimalField(
        source="price_per_purchase_unit",
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
    )
    deliveryLeadTimeDays = serializers.IntegerField(source="delivery_lead_time_days")
    minimumPurchaseQuantity = serializers.DecimalField(
        source="minimum_purchase_quantity",
        max_digits=14,
        decimal_places=3,
        coerce_to_string=True,
    )
    purchaseMultiple = serializers.DecimalField(
        source="purchase_multiple",
        max_digits=14,
        decimal_places=3,
        coerce_to_string=True,
    )
    isAvailable = serializers.BooleanField(source="is_available")
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = SupplierProductCondition
        fields = [
            "id",
            "supplierId",
            "productId",
            "storeId",
            "effectiveFrom",
            "purchaseUnitName",
            "baseUnitsPerPurchaseUnit",
            "pricePerPurchaseUnit",
            "deliveryLeadTimeDays",
            "minimumPurchaseQuantity",
            "purchaseMultiple",
            "isAvailable",
            "createdBy",
            "createdAt",
        ]


class SupplierProductConditionListSerializer(serializers.Serializer):
    supplierId = serializers.UUIDField()
    conditions = SupplierProductConditionSerializer(many=True)
