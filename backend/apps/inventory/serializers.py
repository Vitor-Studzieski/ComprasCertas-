"""Strict API representations for the first online inventory slice."""

import re
from collections.abc import Mapping
from decimal import Decimal

from rest_framework import serializers

from apps.inventory.models import (
    OpeningStockBatch,
    OpeningStockLine,
    OperationalCalendarDefault,
    Product,
    Store,
    StoreOperationalCalendarOverride,
    StoreProduct,
)
from apps.inventory.services.calendar import is_valid_time_zone


class StrictInputMixin:
    """Reject undeclared input fields instead of silently ignoring them."""

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {"non_field_errors": [f"Campos não aceitos: {', '.join(sorted(unknown))}."]}
                )
        return super().to_internal_value(data)


class OperationalCalendarDefaultVersionRequestSerializer(
    StrictInputMixin, serializers.Serializer
):
    timeZone = serializers.CharField(min_length=1, max_length=64)
    operationalDayStart = serializers.TimeField(format="%H:%M", input_formats=["%H:%M"])
    effectiveFrom = serializers.DateField()

    def validate_timeZone(self, value):
        if not is_valid_time_zone(value):
            raise serializers.ValidationError("Informe um fuso horário IANA válido.")
        return value


class OperationalCalendarDefaultVersionSerializer(serializers.ModelSerializer):
    timeZone = serializers.CharField(source="time_zone")
    operationalDayStart = serializers.TimeField(
        source="operational_day_start",
        format="%H:%M",
    )
    effectiveFrom = serializers.DateField(source="effective_from")
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = OperationalCalendarDefault
        fields = ["id", "timeZone", "operationalDayStart", "effectiveFrom", "createdBy", "createdAt"]


class OperationalCalendarDefaultVersionsSerializer(serializers.Serializer):
    versions = OperationalCalendarDefaultVersionSerializer(many=True)


class StoreOperationalCalendarVersionRequestSerializer(
    StrictInputMixin, serializers.Serializer
):
    timeZone = serializers.CharField(
        min_length=1,
        max_length=64,
        required=False,
        allow_null=True,
    )
    operationalDayStart = serializers.TimeField(
        format="%H:%M",
        input_formats=["%H:%M"],
        required=False,
        allow_null=True,
    )
    effectiveFrom = serializers.DateField()

    def validate_timeZone(self, value):
        if value is not None and not is_valid_time_zone(value):
            raise serializers.ValidationError("Informe um fuso horário IANA válido.")
        return value


class StoreOperationalCalendarVersionSerializer(serializers.ModelSerializer):
    timeZone = serializers.CharField(source="time_zone", allow_null=True)
    operationalDayStart = serializers.TimeField(
        source="operational_day_start",
        format="%H:%M",
        allow_null=True,
    )
    effectiveFrom = serializers.DateField(source="effective_from")
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = StoreOperationalCalendarOverride
        fields = ["id", "timeZone", "operationalDayStart", "effectiveFrom", "createdBy", "createdAt"]


class StoreOperationalCalendarSerializer(serializers.Serializer):
    storeId = serializers.UUIDField()
    versions = StoreOperationalCalendarVersionSerializer(many=True)


class EffectiveOperationalCalendarSerializer(serializers.Serializer):
    timeZone = serializers.CharField()
    operationalDayStart = serializers.TimeField(format="%H:%M")


class StoreCalendarOverrideRequestSerializer(StrictInputMixin, serializers.Serializer):
    timeZone = serializers.CharField(
        min_length=1,
        max_length=64,
        required=False,
        allow_null=True,
    )
    operationalDayStart = serializers.TimeField(
        format="%H:%M",
        input_formats=["%H:%M"],
        required=False,
        allow_null=True,
    )
    effectiveFrom = serializers.DateField()

    def validate_timeZone(self, value):
        if value is not None and not is_valid_time_zone(value):
            raise serializers.ValidationError("Informe um fuso horário IANA válido.")
        return value

    def validate_timeZone(self, value):
        if value is not None and not is_valid_time_zone(value):
            raise serializers.ValidationError("Informe um fuso horário IANA válido.")
        return value


class StoreCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=160, trim_whitespace=True)
    calendarOverrides = StoreCalendarOverrideRequestSerializer(required=False)

    def validate_name(self, value):
        if not value:
            raise serializers.ValidationError("Informe o nome da loja.")
        return value


class StoreSerializer(serializers.ModelSerializer):
    isActive = serializers.BooleanField(source="is_active")
    calendarOverrides = serializers.SerializerMethodField()
    effectiveCalendar = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = Store
        fields = ["id", "name", "isActive", "calendarOverrides", "effectiveCalendar", "createdAt"]

    def get_calendarOverrides(self, store):
        versions = store.calendar_overrides.all().order_by("effective_from", "id")
        return StoreOperationalCalendarVersionSerializer(versions, many=True).data

    def get_effectiveCalendar(self, store):
        from apps.inventory.services.calendar import (
            OperationalCalendarMissing,
            current_operational_date,
        )

        try:
            _, calendar = current_operational_date(
                organization_id=store.organization_id,
                store=store,
            )
        except OperationalCalendarMissing:
            return None
        return EffectiveOperationalCalendarSerializer(calendar).data


class StoreListSerializer(serializers.Serializer):
    stores = StoreSerializer(many=True)


class ProductCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    sku = serializers.CharField(min_length=1, max_length=64, trim_whitespace=True)
    name = serializers.CharField(min_length=1, max_length=200, trim_whitespace=True)
    baseUnit = serializers.ChoiceField(choices=Product.Unit.choices)

    def validate_sku(self, value):
        if not value:
            raise serializers.ValidationError("Informe o SKU.")
        return value

    def validate_name(self, value):
        if not value:
            raise serializers.ValidationError("Informe o nome do produto.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    baseUnit = serializers.CharField(source="unit")
    isActive = serializers.BooleanField(source="is_active")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "baseUnit", "isActive", "createdAt"]


class ProductListSerializer(serializers.Serializer):
    products = ProductSerializer(many=True)


class StoreProductAssortmentCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField(source="product_id")
    effectiveFrom = serializers.DateField(source="effective_from")
    effectiveTo = serializers.DateField(source="effective_to", required=False, allow_null=True)


class StoreProductAssortmentCloseRequestSerializer(StrictInputMixin, serializers.Serializer):
    effectiveTo = serializers.DateField(source="effective_to")


class StoreProductAssortmentSerializer(serializers.ModelSerializer):
    productId = serializers.UUIDField(source="product_id")
    sku = serializers.CharField(source="product.sku")
    productName = serializers.CharField(source="product.name")
    baseUnit = serializers.CharField(source="product.unit")
    effectiveFrom = serializers.DateField(source="effective_from")
    effectiveTo = serializers.DateField(source="effective_to", allow_null=True)
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")
    effectiveToSetBy = serializers.UUIDField(source="effective_to_set_by_id", allow_null=True)
    effectiveToSetAt = serializers.DateTimeField(source="effective_to_set_at", allow_null=True)

    class Meta:
        model = StoreProduct
        fields = [
            "id",
            "productId",
            "sku",
            "productName",
            "baseUnit",
            "effectiveFrom",
            "effectiveTo",
            "createdBy",
            "createdAt",
            "effectiveToSetBy",
            "effectiveToSetAt",
        ]


class StoreProductAssortmentListSerializer(serializers.Serializer):
    storeId = serializers.UUIDField()
    assortments = StoreProductAssortmentSerializer(many=True)


class ExactQuantityStringField(serializers.DecimalField):
    pattern = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$")

    def to_internal_value(self, data):
        if not isinstance(data, str) or not self.pattern.fullmatch(data):
            self.fail("invalid")
        return super().to_internal_value(data)

    default_error_messages = {
        **serializers.DecimalField.default_error_messages,
        "invalid": "Informe a quantidade como uma string decimal exata.",
    }


class OpeningStockCountLineRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField(source="product_id")
    quantity = ExactQuantityStringField(max_digits=14, decimal_places=3, min_value=0)


class OpeningStockCountRequestSerializer(StrictInputMixin, serializers.Serializer):
    closedOperationalDate = serializers.DateField(source="closed_operational_date")
    lines = OpeningStockCountLineRequestSerializer(many=True, allow_empty=False)


class OpeningStockCountLineSerializer(serializers.ModelSerializer):
    productId = serializers.UUIDField(source="product_id")
    quantity = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        coerce_to_string=True,
    )

    class Meta:
        model = OpeningStockLine
        fields = ["id", "productId", "quantity"]


class OpeningStockCountSerializer(serializers.ModelSerializer):
    storeId = serializers.UUIDField(source="store_id")
    closedOperationalDate = serializers.DateField(source="closed_operational_date")
    cutoffDate = serializers.DateField(source="cutoff_date")
    source = serializers.CharField()
    lines = OpeningStockCountLineSerializer(many=True)
    createdBy = serializers.UUIDField(source="created_by_id")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = OpeningStockBatch
        fields = [
            "id",
            "storeId",
            "closedOperationalDate",
            "cutoffDate",
            "source",
            "lines",
            "createdBy",
            "createdAt",
        ]


class StockBalanceSerializer(serializers.Serializer):
    productId = serializers.UUIDField(source="product.id")
    sku = serializers.CharField(source="product.sku")
    productName = serializers.CharField(source="product.name")
    baseUnit = serializers.CharField(source="product.unit")
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3, coerce_to_string=True)


class StockBalanceListSerializer(serializers.Serializer):
    storeId = serializers.UUIDField()
    asOfOperationalDate = serializers.DateField()
    balances = StockBalanceSerializer(many=True)
