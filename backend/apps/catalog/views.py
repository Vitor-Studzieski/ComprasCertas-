"""Administrator and buyer APIs for the shared supplier catalog."""

from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
)
from rest_framework.views import APIView

from apps.catalog.serializers import (
    SupplierCreateRequestSerializer,
    SupplierListSerializer,
    SupplierProductConditionCreateRequestSerializer,
    SupplierProductConditionListSerializer,
    SupplierProductConditionQuerySerializer,
    SupplierProductConditionSerializer,
    SupplierSerializer,
    SupplierUpdateRequestSerializer,
)
from apps.catalog.services import (
    InvalidSupplierCondition,
    ProductUnavailable,
    StoreUnavailable,
    SupplierConditionDateConflict,
    SupplierUnavailable,
    create_supplier,
    create_supplier_condition,
    get_supplier,
    list_supplier_conditions,
    list_suppliers,
    update_supplier,
)
from apps.identity.models import Membership
from apps.identity.permissions import MfaForPrivilegedUsers
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    set_active_organization_rls_context,
)


def _problem(code, message, status, fields=None):
    body = {"code": code, "message": message}
    if fields is not None:
        body["fields"] = fields
    return Response(body, status=status)


def _validation_response(errors):
    return _problem("validation_error", "Revise os dados informados.", HTTP_400_BAD_REQUEST, errors)


def _resource_unavailable():
    return _problem(
        "resource_unavailable",
        "O recurso solicitado não está disponível.",
        HTTP_404_NOT_FOUND,
    )


def _active_organization_required():
    return _problem(
        "active_organization_required",
        "Selecione uma organização ativa antes de acessar os dados da rede.",
        HTTP_404_NOT_FOUND,
    )


def _catalog_access_required():
    return _problem(
        "catalog_access_required",
        "Esta operação exige o papel de administrador ou comprador da rede.",
        HTTP_403_FORBIDDEN,
    )


def _catalog_store_scope_required():
    return _problem(
        "catalog_store_access_required",
        "Condições específicas por loja exigem acesso de administrador nesta etapa.",
        HTTP_403_FORBIDDEN,
    )


def _supplier_name_conflict():
    return _problem(
        "supplier_name_conflict",
        "Já existe um fornecedor com esse nome nesta rede.",
        HTTP_409_CONFLICT,
    )


def _supplier_condition_conflict():
    return _problem(
        "supplier_condition_conflict",
        "Já existe uma condição para esse fornecedor, produto, escopo e data de vigência.",
        HTTP_409_CONFLICT,
    )


def _supplier_condition_effective_date_conflict():
    return _problem(
        "supplier_condition_effective_date_conflict",
        "A nova condição deve ter data de vigência posterior à última condição desse escopo.",
        HTTP_409_CONFLICT,
    )


def _violated_constraint(error):
    database_error = getattr(error, "__cause__", None)
    diagnostics = getattr(database_error, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def _organization_and_catalog_access(request):
    try:
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
    except ActiveOrganizationRequired:
        return None, _active_organization_required()

    allowed = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
        role__in=[Membership.Role.ADMINISTRATOR, Membership.Role.BUYER],
    ).exists()
    if not allowed:
        return None, _catalog_access_required()
    return organization_id, None


def _catalog_membership_role(request, organization_id):
    return Membership.objects.filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
    ).values_list("role", flat=True).get()


@method_decorator(csrf_protect, name="dispatch")
class CatalogAPIView(APIView):
    permission_classes = [MfaForPrivilegedUsers]

    def permission_denied(self, request, message=None, code=None):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and str(message) == MfaForPrivilegedUsers.message:
            raise PermissionDenied(detail={"code": "mfa_required", "message": str(message)})
        return super().permission_denied(request, message=message, code=code)

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if (
            isinstance(exc, PermissionDenied)
            and isinstance(exc.detail, dict)
            and exc.detail.get("code") == "mfa_required"
        ):
            response.data = exc.detail
        return response


class SupplierListCreateView(CatalogAPIView):
    def get(self, request):
        try:
            with transaction.atomic():
                _, error = _organization_and_catalog_access(request)
                if error:
                    return error
                suppliers = list_suppliers(request=request)
        except ActiveOrganizationRequired:
            return _active_organization_required()

        return Response(SupplierListSerializer({"suppliers": suppliers}).data, status=HTTP_200_OK)

    def post(self, request):
        serializer = SupplierCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            with transaction.atomic():
                _, error = _organization_and_catalog_access(request)
                if error:
                    return error
                supplier = create_supplier(request=request, values=serializer.validated_data)
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except IntegrityError as error:
            if _violated_constraint(error) == "catalog_supplier_name_ci_unique":
                return _supplier_name_conflict()
            raise

        return Response(SupplierSerializer(supplier).data, status=HTTP_201_CREATED)


class SupplierDetailView(CatalogAPIView):
    def get(self, request, supplier_id):
        try:
            with transaction.atomic():
                _, error = _organization_and_catalog_access(request)
                if error:
                    return error
                supplier = get_supplier(request=request, supplier_id=supplier_id)
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except SupplierUnavailable:
            return _resource_unavailable()

        return Response(SupplierSerializer(supplier).data, status=HTTP_200_OK)

    def patch(self, request, supplier_id):
        serializer = SupplierUpdateRequestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            with transaction.atomic():
                _, error = _organization_and_catalog_access(request)
                if error:
                    return error
                supplier = update_supplier(
                    request=request,
                    supplier_id=supplier_id,
                    values=serializer.validated_data,
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except SupplierUnavailable:
            return _resource_unavailable()
        except IntegrityError as error:
            if _violated_constraint(error) == "catalog_supplier_name_ci_unique":
                return _supplier_name_conflict()
            raise

        return Response(SupplierSerializer(supplier).data, status=HTTP_200_OK)


class SupplierProductConditionsView(CatalogAPIView):
    def get(self, request, supplier_id):
        query_serializer = SupplierProductConditionQuerySerializer(data=request.query_params)
        if not query_serializer.is_valid():
            return _validation_response(query_serializer.errors)

        try:
            with transaction.atomic():
                organization_id, error = _organization_and_catalog_access(request)
                if error:
                    return error
                role = _catalog_membership_role(request, organization_id)
                if role == Membership.Role.BUYER and query_serializer.validated_data.get("store_id") is not None:
                    return _catalog_store_scope_required()
                supplier, conditions = list_supplier_conditions(
                    request=request,
                    supplier_id=supplier_id,
                    product_id=query_serializer.validated_data.get("product_id"),
                    store_id=query_serializer.validated_data.get("store_id"),
                    network_only=role == Membership.Role.BUYER,
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except SupplierUnavailable:
            return _resource_unavailable()
        except ProductUnavailable:
            return _resource_unavailable()
        except StoreUnavailable:
            return _resource_unavailable()

        return Response(
            SupplierProductConditionListSerializer(
                {"supplierId": supplier.pk, "conditions": conditions}
            ).data,
            status=HTTP_200_OK,
        )

    def post(self, request, supplier_id):
        serializer = SupplierProductConditionCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            with transaction.atomic():
                organization_id, error = _organization_and_catalog_access(request)
                if error:
                    return error
                role = _catalog_membership_role(request, organization_id)
                if role == Membership.Role.BUYER and serializer.validated_data.get("store_id") is not None:
                    return _catalog_store_scope_required()
                condition = create_supplier_condition(
                    request=request,
                    supplier_id=supplier_id,
                    values=serializer.validated_data,
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except SupplierUnavailable:
            return _resource_unavailable()
        except ProductUnavailable:
            return _resource_unavailable()
        except StoreUnavailable:
            return _resource_unavailable()
        except SupplierConditionDateConflict:
            return _supplier_condition_effective_date_conflict()
        except InvalidSupplierCondition:
            return _validation_response(
                {"baseUnitsPerPurchaseUnit": ["Produtos por unidade exigem fator e quantidades inteiras."]}
            )
        except IntegrityError as error:
            if _violated_constraint(error) in {
                "catalog_condition_network_version_unique",
                "catalog_condition_store_version_unique",
            }:
                return _supplier_condition_conflict()
            raise

        return Response(SupplierProductConditionSerializer(condition).data, status=HTTP_201_CREATED)
