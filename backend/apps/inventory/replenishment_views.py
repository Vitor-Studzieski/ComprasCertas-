"""Administrator endpoints for replenishment rules and demand evidence."""

from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_409_CONFLICT,
)

from apps.identity.models import Membership
from apps.inventory.models import (
    DailySalesCoverage,
    DailyStockAvailability,
    Product,
    ProductCategory,
    ReplenishmentPolicy,
    Store,
    StoreProduct,
)
from apps.inventory.replenishment_serializers import (
    OutOfStockDayCreateRequestSerializer,
    OutOfStockDaySerializer,
    ReplenishmentPolicyPatchSerializer,
    SalesCoverageCreateRequestSerializer,
    SalesCoverageSerializer,
)
from apps.inventory.services.calendar import OperationalCalendarMissing, current_operational_date
from apps.inventory.services.replenishment import generate_store_recommendations
from apps.inventory.views import (
    InventoryAdministratorAPIView,
    _organization_and_admin,
    _organization_and_catalog_reader,
    _organization_and_store_scope,
    _problem,
    _resource_unavailable,
    _validation_response,
    _violated_constraint,
)


def _store_for_org(organization_id, store_id):
    return Store.objects.filter(
        organization_id=organization_id,
        pk=store_id,
        is_active=True,
    ).first()


def _policy_scope(organization_id, kwargs):
    """Resolve a policy target only after checking every foreign key's tenant."""
    store_id = kwargs.get("store_id")
    product_id = kwargs.get("product_id")
    category_id = kwargs.get("category_id")
    store = None
    product = None
    category = None
    if store_id:
        store = _store_for_org(organization_id, store_id)
        if store is None:
            return None, None, None, None, _resource_unavailable()
    if product_id:
        product = Product.objects.filter(organization_id=organization_id, pk=product_id).first()
        if product is None:
            return None, None, None, None, _resource_unavailable()
    if category_id:
        category = ProductCategory.objects.filter(organization_id=organization_id, pk=category_id).first()
        if category is None:
            return None, None, None, None, _resource_unavailable()
    if store is not None and product is not None and not StoreProduct.objects.filter(
        organization_id=organization_id,
        store_id=store.pk,
        product_id=product.pk,
    ).exists():
        return None, None, None, None, _resource_unavailable()
    return store, product, category, {
        "store": store,
        "product": product,
        "category": category,
    }, None


def _policy_response(policy, scope_name):
    return {
        "scope": scope_name,
        "minimumStock": str(policy.minimum_stock) if policy.minimum_stock is not None else None,
        "safetyDays": policy.safety_days,
        "reviewIntervalDays": policy.review_interval_days,
    }


class ReplenishmentPolicyView(InventoryAdministratorAPIView):
    def _get_policy(self, request, kwargs):
        organization_id, error = _organization_and_admin(request)
        if error:
            return None, None, error
        store, product, category, scope, error = _policy_scope(organization_id, kwargs)
        if error:
            return None, None, error
        policy = ReplenishmentPolicy.objects.filter(
            organization_id=organization_id,
            store=store,
            product=product,
            category=category,
        ).first()
        if store is not None and product is not None:
            scope_name = "store_product"
        elif product is not None:
            scope_name = "product"
        elif category is not None:
            scope_name = "category"
        elif store is not None:
            scope_name = "store"
        else:
            scope_name = "network"
        return (organization_id, scope_name), (scope, policy), None

    def get(self, request, **kwargs):
        with transaction.atomic():
            resolved, result, error = self._get_policy(request, kwargs)
            if error:
                return error
            _, scope_name = resolved
            _, policy = result
            if policy is None:
                return Response({
                    "scope": scope_name,
                    "minimumStock": None,
                    "safetyDays": None,
                    "reviewIntervalDays": None,
                }, status=HTTP_200_OK)
            return Response(_policy_response(policy, scope_name), status=HTTP_200_OK)

    def patch(self, request, **kwargs):
        serializer = ReplenishmentPolicyPatchSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        attrs = serializer.validated_data
        with transaction.atomic():
            resolved, result, error = self._get_policy(request, kwargs)
            if error:
                return error
            organization_id, scope_name = resolved
            scope, policy = result
            if scope_name == "store" and set(attrs) - {"reviewIntervalDays"}:
                return _problem("invalid_policy_scope", "A loja só pode definir o intervalo de revisão.", HTTP_400_BAD_REQUEST)
            if scope_name in ("category", "product", "store_product") and "reviewIntervalDays" in attrs:
                return _problem("invalid_policy_scope", "Esse escopo não define o intervalo de revisão.", HTTP_400_BAD_REQUEST)

            if policy is None:
                try:
                    with transaction.atomic():
                        policy = ReplenishmentPolicy.objects.create(
                            organization_id=organization_id,
                            **scope,
                            created_by=request.user,
                        )
                except IntegrityError:
                    policy = ReplenishmentPolicy.objects.filter(
                        organization_id=organization_id,
                        **scope,
                    ).first()
                if policy is None:
                    return _problem(
                        "replenishment_policy_conflict",
                        "Não foi possível salvar os parâmetros; atualize e tente novamente.",
                        HTTP_409_CONFLICT,
                    )
            field_map = {
                "minimumStock": "minimum_stock",
                "safetyDays": "safety_days",
                "reviewIntervalDays": "review_interval_days",
            }
            for key, value in attrs.items():
                setattr(policy, field_map[key], value)
            policy.save(update_fields=[*{field_map[key] for key in attrs}, "updated_at"])
            return Response(_policy_response(policy, scope_name), status=HTTP_200_OK)


class StoreReplenishmentRecommendationsView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_scope(
                request,
                store_id,
                [Membership.Role.STORE_MANAGER, Membership.Role.BUYER],
            )
            if error:
                return error
            store = _store_for_org(organization_id, store_id)
            if store is None:
                return _resource_unavailable()
            try:
                as_of_date, _ = current_operational_date(
                    organization_id=organization_id,
                    store=store,
                )
            except OperationalCalendarMissing:
                return _problem(
                    "operational_calendar_required",
                    "Configure o fuso e o início do dia operacional antes de calcular recomendações.",
                    HTTP_409_CONFLICT,
                )
            return Response(
                generate_store_recommendations(
                    organization_id=organization_id,
                    store=store,
                    as_of_date=as_of_date,
                ),
                status=HTTP_200_OK,
            )


class StoreSalesCoverageView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_scope(
                request,
                store_id,
                [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR, Membership.Role.BUYER],
            )
            if error:
                return error
            store = _store_for_org(organization_id, store_id)
            if store is None:
                return _resource_unavailable()
            rows = DailySalesCoverage.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            )
            return Response({"coveragePeriods": SalesCoverageSerializer(rows, many=True).data}, status=HTTP_200_OK)

    def post(self, request, store_id):
        serializer = SalesCoverageCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            organization_id, error = _organization_and_store_scope(
                request,
                store_id,
                [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR],
            )
            if error:
                return error
            store = _store_for_org(organization_id, store_id)
            if store is None:
                return _resource_unavailable()
            try:
                current_date, _ = current_operational_date(
                    organization_id=organization_id,
                    store=store,
                )
            except OperationalCalendarMissing:
                return _problem(
                    "operational_calendar_required",
                    "Configure o fuso e o início do dia operacional antes de declarar vendas completas.",
                    HTTP_409_CONFLICT,
                )
            if serializer.validated_data["operationalDateTo"] > current_date:
                return _problem(
                    "sales_coverage_date_in_future",
                    "O período completo não pode terminar em uma data operacional futura.",
                    HTTP_400_BAD_REQUEST,
                )
            try:
                with transaction.atomic():
                    row = DailySalesCoverage.objects.create(
                        organization_id=organization_id,
                        store=store,
                        operational_date_from=serializer.validated_data["operationalDateFrom"],
                        operational_date_to=serializer.validated_data["operationalDateTo"],
                        created_by=request.user,
                    )
            except IntegrityError:
                return _problem(
                    "sales_coverage_conflict",
                    "Esse período completo já foi declarado para a loja.",
                    HTTP_409_CONFLICT,
                )
            return Response(SalesCoverageSerializer(row).data, status=HTTP_201_CREATED)


class StoreOutOfStockDaysView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_store_scope(
                request,
                store_id,
                [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR, Membership.Role.BUYER],
            )
            if error:
                return error
            store = _store_for_org(organization_id, store_id)
            if store is None:
                return _resource_unavailable()
            rows = DailyStockAvailability.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).select_related("product")
            return Response({"outOfStockDays": OutOfStockDaySerializer(rows, many=True).data}, status=HTTP_200_OK)

    def post(self, request, store_id):
        serializer = OutOfStockDayCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            organization_id, error = _organization_and_store_scope(
                request,
                store_id,
                [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR],
            )
            if error:
                return error
            store = _store_for_org(organization_id, store_id)
            if store is None:
                return _resource_unavailable()
            try:
                current_date, _ = current_operational_date(
                    organization_id=organization_id,
                    store=store,
                )
            except OperationalCalendarMissing:
                return _problem(
                    "operational_calendar_required",
                    "Configure o calendário operacional antes de marcar dias sem estoque.",
                    HTTP_409_CONFLICT,
                )
            if serializer.validated_data["operationalDate"] > current_date:
                return _problem(
                    "out_of_stock_date_in_future",
                    "Não é possível marcar uma data operacional futura como sem estoque.",
                    HTTP_400_BAD_REQUEST,
                )
            product = Product.objects.filter(
                organization_id=organization_id,
                pk=serializer.validated_data["productId"],
            ).first()
            if product is None or not StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                product_id=product.pk,
                effective_from__lte=serializer.validated_data["operationalDate"],
            ).filter(
                Q(effective_to__isnull=True)
                | Q(effective_to__gt=serializer.validated_data["operationalDate"])
            ).exists():
                return _resource_unavailable()
            try:
                with transaction.atomic():
                    row = DailyStockAvailability.objects.create(
                        organization_id=organization_id,
                        store=store,
                        product=product,
                        operational_date=serializer.validated_data["operationalDate"],
                        created_by=request.user,
                    )
            except IntegrityError:
                return _problem(
                    "out_of_stock_day_conflict",
                    "Esse dia já foi marcado como sem estoque para o produto.",
                    HTTP_409_CONFLICT,
                )
            return Response(OutOfStockDaySerializer(row).data, status=HTTP_201_CREATED)
