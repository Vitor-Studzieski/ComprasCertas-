"""HTTP endpoints for per-store purchasing and budget settings."""

from django.db import IntegrityError, transaction
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_409_CONFLICT,
)

from apps.inventory.services.calendar import OperationalCalendarMissing
from apps.inventory.models import Store
from apps.inventory.views import (
    InventoryAdministratorAPIView,
    _organization_and_admin,
    _problem,
    _resource_unavailable,
    _validation_response,
)
from apps.identity.models import Membership
from apps.identity.services.access import StoreAccessDenied, require_store_access
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    set_active_organization_rls_context,
)
from apps.purchasing.serializers import (
    PurchaseBudgetPatchSerializer,
    PurchaseOrderApproveRequestSerializer,
    PurchaseOrderCancelRemainingRequestSerializer,
    PurchaseOrderDraftCreateRequestSerializer,
    PurchaseOrderDraftPatchRequestSerializer,
    PurchaseReceiptCreateRequestSerializer,
)
from apps.purchasing.services.orders import (
    PurchaseBudgetExceeded,
    PurchaseBudgetRequired,
    PurchaseConflict,
    PurchaseUnavailable,
    PurchaseValidationError,
    approve_purchase_order,
    cancel_purchase_order_remaining,
    create_purchase_order_draft,
    edit_purchase_order_draft,
    get_budget_settings,
    get_purchase_order,
    list_store_orders,
    patch_budget_settings,
    receive_purchase_order,
    review_purchase_order,
    _receipt_repr,
)
from apps.purchasing.models import PurchaseReceipt


def _purchase_error(exc):
    if isinstance(exc, PurchaseUnavailable):
        return _resource_unavailable()
    if isinstance(exc, PurchaseBudgetRequired):
        return _problem(
            "purchase_budget_required",
            "Configure os limites semanal e mensal da rede antes de aprovar pedidos.",
            HTTP_409_CONFLICT,
        )
    if isinstance(exc, PurchaseBudgetExceeded):
        return _problem(
            "purchase_budget_exceeded",
            "O pedido ultrapassa um ou mais limites; informe a justificativa de exceção para continuar.",
            HTTP_409_CONFLICT,
            {
                "weeklyLimit": str(exc.weekly_limit),
                "weeklyAfter": str(exc.weekly_after),
                "monthlyLimit": str(exc.monthly_limit),
                "monthlyAfter": str(exc.monthly_after),
            },
        )
    if isinstance(exc, PurchaseValidationError):
        return _problem("purchase_validation_error", str(exc), HTTP_400_BAD_REQUEST)
    if isinstance(exc, PurchaseConflict):
        if str(exc) == "stale":
            return _problem(
                "purchase_order_stale",
                "Os dados mudaram desde a criação ou revisão; gere um novo rascunho e confira as quantidades.",
                HTTP_409_CONFLICT,
            )
        return _problem(
            "purchase_order_conflict",
            "O estado do pedido não permite esta operação.",
            HTTP_409_CONFLICT,
        )
    if isinstance(exc, OperationalCalendarMissing):
        return _problem(
            "operational_calendar_required",
            "Configure o fuso e o início do dia operacional antes de operar compras nesta loja.",
            HTTP_409_CONFLICT,
        )
    if isinstance(exc, IntegrityError):
        return _problem(
            "purchase_order_conflict",
            "Uma alteração concorrente impediu a operação; atualize o pedido e tente novamente.",
            HTTP_409_CONFLICT,
        )
    raise exc


def _organization_and_store_access(request, store_id, allowed_roles):
    try:
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
    except ActiveOrganizationRequired:
        return None, _resource_unavailable()
    if not Store.objects.filter(
        organization_id=organization_id,
        pk=store_id,
        is_active=True,
    ).exists():
        return None, _resource_unavailable()
    try:
        require_store_access(
            organization_id=organization_id,
            user=request.user,
            store_id=store_id,
            allowed_roles=allowed_roles,
        )
    except StoreAccessDenied:
        return None, _problem(
            "store_access_required",
            "Você não tem acesso a esta loja nesta rede.",
            HTTP_403_FORBIDDEN,
        )
    return organization_id, None


STORE_READ_ROLES = [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR, Membership.Role.BUYER]
STORE_MANAGER_ROLES = [Membership.Role.STORE_MANAGER]
STORE_DRAFT_CREATE_ROLES = [Membership.Role.STORE_MANAGER, Membership.Role.BUYER]
STORE_RECEIPT_ROLES = [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR]


class StorePurchaseOrderListCreateView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_access(request, store_id, STORE_READ_ROLES)
            if error:
                return error
            try:
                return Response(list_store_orders(organization_id=organization_id, store_id=store_id), status=HTTP_200_OK)
            except PurchaseUnavailable as exc:
                return _purchase_error(exc)

    def post(self, request, store_id):
        serializer = PurchaseOrderDraftCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            organization_id, error = _organization_and_store_access(request, store_id, STORE_DRAFT_CREATE_ROLES)
            if error:
                return error
            try:
                order = create_purchase_order_draft(
                    request=request,
                    store_id=store_id,
                    supplier_id=serializer.validated_data["supplierId"],
                    items=serializer.validated_data["items"],
                )
            except (PurchaseUnavailable, PurchaseValidationError, OperationalCalendarMissing, IntegrityError) as exc:
                return _purchase_error(exc)
            return Response(order, status=HTTP_201_CREATED)


class PurchaseOrderDetailView(InventoryAdministratorAPIView):
    def get(self, request, store_id, order_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_access(request, store_id, STORE_READ_ROLES)
            if error:
                return error
            try:
                order = get_purchase_order(
                    organization_id=organization_id,
                    store_id=store_id,
                    order_id=order_id,
                )
            except PurchaseUnavailable as exc:
                return _purchase_error(exc)
            from apps.purchasing.services.orders import _order_repr
            return Response(_order_repr(order), status=HTTP_200_OK)

    def patch(self, request, store_id, order_id):
        serializer = PurchaseOrderDraftPatchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            organization_id, error = _organization_and_store_access(request, store_id, STORE_MANAGER_ROLES)
            if error:
                return error
            try:
                order = edit_purchase_order_draft(
                    request=request,
                    store_id=store_id,
                    order_id=order_id,
                    supplier_id=serializer.validated_data["supplierId"],
                    items=serializer.validated_data["items"],
                    change_reason=serializer.validated_data.get("changeReason", ""),
                )
            except (PurchaseUnavailable, PurchaseConflict, PurchaseValidationError, OperationalCalendarMissing, IntegrityError) as exc:
                return _purchase_error(exc)
            return Response(order, status=HTTP_200_OK)


class PurchaseOrderReviewView(InventoryAdministratorAPIView):
    def post(self, request, store_id, order_id):
        if request.data not in ({}, None):
            return _validation_response({"non_field_errors": ["A revisão não recebe campos."]})
        with transaction.atomic():
            _, error = _organization_and_store_access(request, store_id, STORE_DRAFT_CREATE_ROLES)
            if error:
                return error
            try:
                order, stale = review_purchase_order(request=request, store_id=store_id, order_id=order_id)
            except (PurchaseUnavailable, PurchaseConflict, OperationalCalendarMissing, IntegrityError) as exc:
                return _purchase_error(exc)
            if stale:
                return _purchase_error(PurchaseConflict("stale"))
            return Response(order, status=HTTP_200_OK)


class PurchaseOrderApproveView(InventoryAdministratorAPIView):
    def post(self, request, store_id, order_id):
        serializer = PurchaseOrderApproveRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _organization_and_store_access(request, store_id, STORE_MANAGER_ROLES)
            if error:
                return error
            try:
                order, stale = approve_purchase_order(
                    request=request,
                    store_id=store_id,
                    order_id=order_id,
                    budget_exception_reason=serializer.validated_data.get("budgetExceptionReason", ""),
                )
            except (
                PurchaseUnavailable,
                PurchaseConflict,
                PurchaseBudgetRequired,
                PurchaseBudgetExceeded,
                OperationalCalendarMissing,
                IntegrityError,
            ) as exc:
                return _purchase_error(exc)
            if stale:
                return _purchase_error(PurchaseConflict("stale"))
            return Response(order, status=HTTP_200_OK)


class PurchaseOrderReceiptListCreateView(InventoryAdministratorAPIView):
    def get(self, request, store_id, order_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_access(request, store_id, STORE_READ_ROLES)
            if error:
                return error
            try:
                order = get_purchase_order(
                    organization_id=organization_id,
                    store_id=store_id,
                    order_id=order_id,
                )
            except PurchaseUnavailable as exc:
                return _purchase_error(exc)
            receipts = PurchaseReceipt.objects.filter(
                organization_id=organization_id,
                order=order,
            ).prefetch_related("lines__order_item__product")
            return Response({"receipts": [_receipt_repr(row) for row in receipts]}, status=HTTP_200_OK)

    def post(self, request, store_id, order_id):
        serializer = PurchaseReceiptCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _organization_and_store_access(request, store_id, STORE_RECEIPT_ROLES)
            if error:
                return error
            try:
                receipt, _ = receive_purchase_order(
                    request=request,
                    store_id=store_id,
                    order_id=order_id,
                    supplier_reference=serializer.validated_data.get("supplierReference", ""),
                    items=serializer.validated_data["items"],
                    budget_exception_reason=serializer.validated_data.get("budgetExceptionReason", ""),
                )
            except (
                PurchaseUnavailable,
                PurchaseConflict,
                PurchaseBudgetRequired,
                PurchaseBudgetExceeded,
                PurchaseValidationError,
                OperationalCalendarMissing,
                IntegrityError,
            ) as exc:
                return _purchase_error(exc)
            return Response(receipt, status=HTTP_201_CREATED)


class PurchaseOrderCancelRemainingView(InventoryAdministratorAPIView):
    def post(self, request, store_id, order_id):
        serializer = PurchaseOrderCancelRemainingRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _organization_and_store_access(request, store_id, STORE_MANAGER_ROLES)
            if error:
                return error
            try:
                order = cancel_purchase_order_remaining(
                    request=request,
                    store_id=store_id,
                    order_id=order_id,
                    reason=serializer.validated_data["reason"],
                )
            except (PurchaseUnavailable, PurchaseConflict, IntegrityError) as exc:
                return _purchase_error(exc)
            return Response(order, status=HTTP_200_OK)


class PurchaseBudgetSettingsView(InventoryAdministratorAPIView):
    def get(self, request, store_id=None):
        with transaction.atomic():
            if store_id is None:
                organization_id, error = _organization_and_admin(request)
            else:
                organization_id, error = _organization_and_store_access(request, store_id, STORE_READ_ROLES)
            if error:
                return error
            try:
                return Response(
                    get_budget_settings(organization_id=organization_id, store_id=store_id),
                    status=HTTP_200_OK,
                )
            except PurchaseUnavailable as exc:
                return _purchase_error(exc)

    def patch(self, request, store_id=None):
        serializer = PurchaseBudgetPatchSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            _, error = _organization_and_admin(request)
            if error:
                return error
            try:
                result = patch_budget_settings(
                    request=request,
                    store_id=store_id,
                    values=serializer.validated_data,
                )
            except (PurchaseUnavailable, PurchaseBudgetRequired, PurchaseValidationError, IntegrityError) as exc:
                return _purchase_error(exc)
            return Response(result, status=HTTP_200_OK)
