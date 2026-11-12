"""Strict API input schemas for budgets, purchase orders, and receipts."""

from collections.abc import Mapping
import re

from rest_framework import serializers

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


class ExactMoneyStringField(serializers.DecimalField):
    pattern = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")

    def to_internal_value(self, data):
        if data is None and self.allow_null:
            return None
        if not isinstance(data, str) or not self.pattern.fullmatch(data):
            raise serializers.ValidationError("Informe o valor como uma string decimal exata.")
        return super().to_internal_value(data)


class PurchaseBudgetPatchSerializer(StrictInputMixin, serializers.Serializer):
    weeklyLimit = ExactMoneyStringField(
        max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True,
    )
    monthlyLimit = ExactMoneyStringField(
        max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True,
    )
    weekStartsOn = serializers.IntegerField(min_value=0, max_value=6, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Informe ao menos um parâmetro para alterar.")
        return attrs


class PurchaseOrderDraftItemRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField()


class PurchaseOrderDraftCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    supplierId = serializers.UUIDField()
    items = PurchaseOrderDraftItemRequestSerializer(many=True, allow_empty=False)

    def validate_items(self, value):
        ids = [row["productId"] for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Cada produto pode aparecer uma única vez no pedido.")
        return value


class PurchaseOrderDraftItemPatchRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField()
    quantity = ExactQuantityStringField(max_digits=14, decimal_places=3, min_value=0)


class PurchaseOrderDraftPatchRequestSerializer(StrictInputMixin, serializers.Serializer):
    supplierId = serializers.UUIDField()
    items = PurchaseOrderDraftItemPatchRequestSerializer(many=True, allow_empty=False)
    changeReason = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")

    def validate_items(self, value):
        ids = [row["productId"] for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Cada produto pode aparecer uma única vez no pedido.")
        return value


class PurchaseOrderApproveRequestSerializer(StrictInputMixin, serializers.Serializer):
    budgetExceptionReason = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class PurchaseReceiptItemRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField()
    physicalQuantity = ExactQuantityStringField(max_digits=14, decimal_places=3, min_value=0)
    actualUnitPrice = ExactMoneyStringField(max_digits=14, decimal_places=2, min_value=0)


class PurchaseReceiptCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    supplierReference = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    budgetExceptionReason = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")
    items = PurchaseReceiptItemRequestSerializer(many=True, allow_empty=False)

    def validate_items(self, value):
        ids = [row["productId"] for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Cada produto pode aparecer uma única vez no recebimento.")
        if any(row["physicalQuantity"] <= 0 for row in value):
            raise serializers.ValidationError("A quantidade física deve ser maior que zero.")
        return value


class PurchaseOrderCancelRemainingRequestSerializer(StrictInputMixin, serializers.Serializer):
    reason = serializers.CharField(min_length=3, max_length=1000, trim_whitespace=True)

    def validate_reason(self, value):
        if not value:
            raise serializers.ValidationError("Informe o motivo do cancelamento do saldo pendente.")
        return value


class ExpectedDeliveryCreateRequestSerializer(StrictInputMixin, serializers.Serializer):
    productId = serializers.UUIDField()
    quantity = ExactQuantityStringField(max_digits=14, decimal_places=3, min_value=0)
    etaDate = serializers.DateField(input_formats=["%Y-%m-%d"])


class ExpectedDeliveryPatchRequestSerializer(StrictInputMixin, serializers.Serializer):
    etaDate = serializers.DateField(input_formats=["%Y-%m-%d"])
    reason = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)


class DeliveryRiskAcknowledgeRequestSerializer(StrictInputMixin, serializers.Serializer):
    actionNote = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)


class DeliveryRiskResolveRequestSerializer(StrictInputMixin, serializers.Serializer):
    resolution = serializers.ChoiceField(choices=["resolved", "justified"])
    reason = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)
