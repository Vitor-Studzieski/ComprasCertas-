"""Administrator-only store setup, opening-stock, and daily-sales API."""

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
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

from apps.identity.models import Membership
from apps.identity.permissions import MfaForPrivilegedUsers
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    set_active_organization_rls_context,
)
from apps.inventory.models import (
    OperationalCalendarDefault,
    Product,
    ProductCategory,
    Store,
    StoreOperationalCalendarOverride,
    StoreProduct,
)
from apps.inventory.serializers import (
    OpeningStockCountRequestSerializer,
    OpeningStockCountSerializer,
    OperationalCalendarDefaultVersionRequestSerializer,
    OperationalCalendarDefaultVersionSerializer,
    OperationalCalendarDefaultVersionsSerializer,
    ProductCreateRequestSerializer,
    ProductListSerializer,
    ProductSerializer,
    StockBalanceListSerializer,
    StoreCreateRequestSerializer,
    StoreListSerializer,
    StoreOperationalCalendarVersionRequestSerializer,
    StoreOperationalCalendarVersionSerializer,
    StoreProductAssortmentCloseRequestSerializer,
    StoreProductAssortmentCreateRequestSerializer,
    StoreProductAssortmentSerializer,
    StoreSerializer,
    DailyProductSaleCreateRequestSerializer,
    DailyProductSaleQuerySerializer,
    DailyProductSaleSerializer,
    DailyProductSalesListSerializer,
    SalesCorrectionListSerializer,
    SalesCorrectionRequestSerializer,
    SalesCorrectionSerializer,
)
from apps.inventory.replenishment_serializers import (
    ProductCategoryCreateRequestSerializer,
    ProductCategorySerializer,
)
from apps.inventory.services.calendar import (
    CalendarVersionConflict,
    OperationalCalendarMissing,
    create_default_calendar_version,
    create_store_calendar_override,
)
from apps.inventory.services.opening_stock import (
    InvalidOpeningStock,
    OpeningDateInFuture,
    OpeningStockAlreadyRecorded,
    ProductNotAssorted,
    ProductUnavailable,
    StoreUnavailable,
    confirm_opening_stock,
    get_store_balances,
)
from apps.inventory.services.daily_sales import (
    DailySaleAlreadyRecorded,
    DailySaleDateInFuture,
    DailySaleUnavailable,
    IdempotencyConflict,
    InvalidDailySale,
    OpeningStockRequired,
    ProductNotAssorted as DailySaleProductNotAssorted,
    ProductUnavailable as DailySaleProductUnavailable,
    StaleDailySale,
    StoreUnavailable as DailySaleStoreUnavailable,
    correct_daily_sale,
    create_daily_sale,
    list_daily_sales,
    list_sales_corrections,
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


def _administrator_required():
    return _problem(
        "administrator_required",
        "Esta operação exige o papel de administrador da rede.",
        HTTP_403_FORBIDDEN,
    )


def _calendar_required():
    return _problem(
        "operational_calendar_required",
        "Configure um fuso horário e o início do dia operacional efetivos para esta data.",
        HTTP_409_CONFLICT,
    )


def _calendar_version_conflict():
    return _problem(
        "operational_calendar_version_conflict",
        "A data efetiva já existe ou alteraria um período histórico.",
        HTTP_409_CONFLICT,
    )


def _assortment_conflict():
    return _problem(
        "assortment_conflict",
        "O produto já possui um vínculo nesta loja ou o vínculo já foi encerrado.",
        HTTP_409_CONFLICT,
    )


def _opening_stock_required():
    return _problem(
        "opening_stock_required",
        "Registre a contagem inicial para definir a data de corte desta loja.",
        HTTP_409_CONFLICT,
    )


def _daily_sale_conflict():
    return _problem(
        "daily_sale_conflict",
        "Já existe um total para este produto, loja e data operacional. Use a correção explícita.",
        HTTP_409_CONFLICT,
    )


def _idempotency_conflict():
    return _problem(
        "idempotency_conflict",
        "O operationId já foi usado com dados diferentes.",
        HTTP_409_CONFLICT,
    )


def _stale_daily_sale():
    return _problem(
        "daily_sale_quantity_conflict",
        "O total mudou desde a consulta. Atualize os dados e tente a correção novamente.",
        HTTP_409_CONFLICT,
    )


def _violated_constraint(error):
    database_error = getattr(error, "__cause__", None)
    diagnostics = getattr(database_error, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def _organization_and_admin(request):
    """Resolve the tenant from session and lock its active administrator membership."""
    try:
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
    except ActiveOrganizationRequired:
        return None, _active_organization_required()

    is_admin = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
        role=Membership.Role.ADMINISTRATOR,
    ).exists()
    if not is_admin:
        return None, _administrator_required()
    return organization_id, None


def _organization_and_catalog_reader(request):
    """Resolve the tenant for read-only catalog access by admins and buyers."""
    try:
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
    except ActiveOrganizationRequired:
        return None, _active_organization_required()

    can_read_catalog = Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
        role__in=[Membership.Role.ADMINISTRATOR, Membership.Role.BUYER],
    ).exists()
    if not can_read_catalog:
        return None, _problem(
            "catalog_access_required",
            "Esta operação exige o papel de administrador ou comprador da rede.",
            HTTP_403_FORBIDDEN,
        )
    return organization_id, None


def _store_for_organization(*, organization_id, store_id, lock=False):
    stores = Store.objects.filter(organization_id=organization_id)
    if lock:
        stores = stores.select_for_update()
    try:
        return stores.get(pk=store_id, is_active=True)
    except Store.DoesNotExist:
        return None


@method_decorator(csrf_protect, name="dispatch")
class InventoryAdministratorAPIView(APIView):
    # This is an onboarding-slice role policy; future store roles must add scoped access.
    permission_classes = [MfaForPrivilegedUsers]

    def permission_denied(self, request, message=None, code=None):
        user = getattr(request, "user", None)
        if (
            user
            and user.is_authenticated
            and str(message) == MfaForPrivilegedUsers.message
        ):
            raise PermissionDenied(
                detail={"code": "mfa_required", "message": str(message)}
            )
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


class OrganizationOperationalCalendarView(InventoryAdministratorAPIView):
    def get(self, request):
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            versions = OperationalCalendarDefault.objects.filter(
                organization_id=organization_id,
            ).order_by("effective_from", "id")
            return Response(
                OperationalCalendarDefaultVersionsSerializer({"versions": versions}).data,
                status=HTTP_200_OK,
            )


class OrganizationOperationalCalendarVersionView(InventoryAdministratorAPIView):
    def post(self, request):
        serializer = OperationalCalendarDefaultVersionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        values = serializer.validated_data
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            try:
                version = create_default_calendar_version(
                    organization_id=organization_id,
                    effective_from=values["effectiveFrom"],
                    time_zone=values["timeZone"],
                    operational_day_start=values["operationalDayStart"],
                    actor=request.user,
                )
            except CalendarVersionConflict:
                return _calendar_version_conflict()
        return Response(
            OperationalCalendarDefaultVersionSerializer(version).data,
            status=HTTP_201_CREATED,
        )


class StoreOperationalCalendarVersionView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            store = _store_for_organization(
                organization_id=organization_id,
                store_id=store_id,
            )
            if store is None:
                return _resource_unavailable()
            versions = StoreOperationalCalendarOverride.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).order_by("effective_from", "id")
            return Response(
                {"storeId": store.pk, "versions": StoreOperationalCalendarVersionSerializer(versions, many=True).data},
                status=HTTP_200_OK,
            )

    def post(self, request, store_id):
        serializer = StoreOperationalCalendarVersionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            store = _store_for_organization(
                organization_id=organization_id,
                store_id=store_id,
                lock=True,
            )
            if store is None:
                return _resource_unavailable()
            try:
                version = create_store_calendar_override(
                    organization_id=organization_id,
                    store=store,
                    effective_from=values["effectiveFrom"],
                    time_zone=values.get("timeZone"),
                    operational_day_start=values.get("operationalDayStart"),
                    actor=request.user,
                )
            except CalendarVersionConflict:
                return _calendar_version_conflict()
        return Response(
            StoreOperationalCalendarVersionSerializer(version).data,
            status=HTTP_201_CREATED,
        )


class StoreListCreateView(InventoryAdministratorAPIView):
    def get(self, request):
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            stores = Store.objects.filter(organization_id=organization_id).prefetch_related(
                "calendar_overrides"
            ).order_by("name", "id")
            return Response(StoreListSerializer({"stores": stores}).data, status=HTTP_200_OK)

    def post(self, request):
        serializer = StoreCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            try:
                # Keep the store and optional initial override all-or-nothing.
                with transaction.atomic():
                    store = Store.objects.create(
                        organization_id=organization_id,
                        name=values["name"],
                    )
                    override_values = values.get("calendarOverrides")
                    if override_values is not None:
                        create_store_calendar_override(
                            organization_id=organization_id,
                            store=store,
                            effective_from=override_values["effectiveFrom"],
                            time_zone=override_values.get("timeZone"),
                            operational_day_start=override_values.get("operationalDayStart"),
                            actor=request.user,
                        )
            except IntegrityError:
                return _problem(
                    "store_name_conflict",
                    "Já existe uma loja com esse nome nesta rede.",
                    HTTP_409_CONFLICT,
                )
            except CalendarVersionConflict:
                return _calendar_version_conflict()

            store = Store.objects.prefetch_related("calendar_overrides").get(
                organization_id=organization_id,
                pk=store.pk,
            )
            return Response(StoreSerializer(store).data, status=HTTP_201_CREATED)


class ProductListCreateView(InventoryAdministratorAPIView):
    def get(self, request):
        with transaction.atomic():
            organization_id, error = _organization_and_catalog_reader(request)
            if error:
                return error
            products = Product.objects.filter(organization_id=organization_id).select_related("category").order_by("sku", "id")
            return Response(ProductListSerializer({"products": products}).data, status=HTTP_200_OK)

    def post(self, request):
        serializer = ProductCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            category = None
            if values.get("category_id") is not None:
                category = ProductCategory.objects.filter(
                    organization_id=organization_id,
                    pk=values["category_id"],
                ).first()
                if category is None:
                    return _resource_unavailable()
            try:
                with transaction.atomic():
                    product = Product.objects.create(
                        organization_id=organization_id,
                        sku=values["sku"],
                        name=values["name"],
                        unit=values["baseUnit"],
                        category=category,
                    )
            except IntegrityError:
                return _problem(
                    "product_sku_conflict",
                    "Esse SKU já está cadastrado nesta rede.",
                    HTTP_409_CONFLICT,
                )
            return Response(ProductSerializer(product).data, status=HTTP_201_CREATED)


class ProductCategoryListCreateView(InventoryAdministratorAPIView):
    def get(self, request):
        with transaction.atomic():
            organization_id, error = _organization_and_catalog_reader(request)
            if error:
                return error
            categories = ProductCategory.objects.filter(
                organization_id=organization_id,
            ).order_by("name", "id")
            return Response(
                {"categories": ProductCategorySerializer(categories, many=True).data},
                status=HTTP_200_OK,
            )

    def post(self, request):
        serializer = ProductCategoryCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            try:
                with transaction.atomic():
                    category = ProductCategory.objects.create(
                        organization_id=organization_id,
                        name=serializer.validated_data["name"],
                    )
            except IntegrityError:
                return _problem(
                    "product_category_name_conflict",
                    "Já existe uma categoria com esse nome nesta rede.",
                    HTTP_409_CONFLICT,
                )
            return Response(ProductCategorySerializer(category).data, status=HTTP_201_CREATED)


def _assortments_overlap(
    *, organization_id, store_id, product_id, effective_from, effective_to, exclude_id=None
):
    existing = StoreProduct.objects.filter(
        organization_id=organization_id,
        store_id=store_id,
        product_id=product_id,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=effective_from))
    if exclude_id is not None:
        existing = existing.exclude(pk=exclude_id)
    if effective_to is not None:
        existing = existing.filter(effective_from__lt=effective_to)
    return existing.exists()


class StoreProductAssortmentListCreateView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            store = _store_for_organization(
                organization_id=organization_id,
                store_id=store_id,
            )
            if store is None:
                return _resource_unavailable()
            assortments = StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).select_related("product", "created_by", "effective_to_set_by").order_by(
                "product__sku", "effective_from", "id"
            )
            data = {
                "storeId": store.pk,
                "assortments": StoreProductAssortmentSerializer(assortments, many=True).data,
            }
            return Response(data, status=HTTP_200_OK)

    def post(self, request, store_id):
        serializer = StoreProductAssortmentCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            store = _store_for_organization(
                organization_id=organization_id,
                store_id=store_id,
                lock=True,
            )
            if store is None:
                return _resource_unavailable()
            try:
                product = Product.objects.get(
                    organization_id=organization_id,
                    pk=values["product_id"],
                    is_active=True,
                )
            except Product.DoesNotExist:
                return _resource_unavailable()

            effective_from = values["effective_from"]
            effective_to = values.get("effective_to")
            if effective_to is not None and effective_to <= effective_from:
                return _validation_response(
                    {"effectiveTo": ["A data final deve ser posterior à data inicial."]}
                )
            if _assortments_overlap(
                organization_id=organization_id,
                store_id=store.pk,
                product_id=product.pk,
                effective_from=effective_from,
                effective_to=effective_to,
            ):
                return _assortment_conflict()

            now = timezone.now() if effective_to is not None else None
            try:
                with transaction.atomic():
                    assortment = StoreProduct.objects.create(
                        organization_id=organization_id,
                        store=store,
                        product=product,
                        effective_from=effective_from,
                        effective_to=effective_to,
                        created_by=request.user,
                        effective_to_set_by=request.user if effective_to is not None else None,
                        effective_to_set_at=now,
                    )
            except IntegrityError:
                return _assortment_conflict()
            return Response(
                StoreProductAssortmentSerializer(assortment).data,
                status=HTTP_201_CREATED,
            )


class StoreProductAssortmentCloseView(InventoryAdministratorAPIView):
    def patch(self, request, store_id, assortment_id):
        serializer = StoreProductAssortmentCloseRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        effective_to = serializer.validated_data["effective_to"]

        with transaction.atomic():
            organization_id, error = _organization_and_admin(request)
            if error:
                return error
            store = _store_for_organization(
                organization_id=organization_id,
                store_id=store_id,
                lock=True,
            )
            if store is None:
                return _resource_unavailable()
            try:
                assortment = StoreProduct.objects.select_for_update().select_related("product").get(
                    organization_id=organization_id,
                    store_id=store.pk,
                    pk=assortment_id,
                )
            except StoreProduct.DoesNotExist:
                return _resource_unavailable()

            if assortment.effective_to is not None:
                return _assortment_conflict()
            if effective_to <= assortment.effective_from:
                return _validation_response(
                    {"effectiveTo": ["A data final deve ser posterior à data inicial."]}
                )
            if _assortments_overlap(
                organization_id=organization_id,
                store_id=store.pk,
                product_id=assortment.product_id,
                effective_from=assortment.effective_from,
                effective_to=effective_to,
                exclude_id=assortment.pk,
            ):
                return _assortment_conflict()

            assortment.effective_to = effective_to
            assortment.effective_to_set_by = request.user
            assortment.effective_to_set_at = timezone.now()
            assortment.save(
                update_fields=["effective_to", "effective_to_set_by", "effective_to_set_at"]
            )
            assortment = StoreProduct.objects.select_related(
                "product", "created_by", "effective_to_set_by"
            ).get(pk=assortment.pk)
            return Response(StoreProductAssortmentSerializer(assortment).data, status=HTTP_200_OK)


class OpeningStockCountCreateView(InventoryAdministratorAPIView):
    def post(self, request, store_id):
        serializer = OpeningStockCountRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        try:
            with transaction.atomic():
                _, error = _organization_and_admin(request)
                if error:
                    return error
                batch, _ = confirm_opening_stock(
                    request=request,
                    store_id=store_id,
                    closed_operational_date=values["closed_operational_date"],
                    lines=[
                        {"product_id": line["product_id"], "quantity": line["quantity"]}
                        for line in values["lines"]
                    ],
                )
                batch = type(batch).objects.prefetch_related("lines").get(pk=batch.pk)
                response_data = OpeningStockCountSerializer(batch).data
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except OperationalCalendarMissing:
            return _calendar_required()
        except StoreUnavailable:
            return _resource_unavailable()
        except ProductUnavailable:
            return _resource_unavailable()
        except ProductNotAssorted:
            return _validation_response(
                {"lines": ["Inclua exatamente uma contagem para cada produto ativo no sortimento."]}
            )
        except InvalidOpeningStock:
            return _validation_response(
                {
                    "lines": [
                        "A contagem deve incluir cada produto ativo uma vez, sem negativos, com precisão permitida pela unidade."
                    ]
                }
            )
        except OpeningStockAlreadyRecorded:
            return _problem(
                "opening_stock_already_recorded",
                "A contagem inicial desta loja já foi registrada.",
                HTTP_409_CONFLICT,
            )
        except OpeningDateInFuture:
            return _validation_response(
                {"closedOperationalDate": ["A data fechada não pode ser futura no calendário da loja."]}
            )

        return Response(response_data, status=HTTP_201_CREATED)


class StoreStockBalanceListView(InventoryAdministratorAPIView):
    def get(self, request, store_id):
        try:
            with transaction.atomic():
                organization_id, error = _organization_and_admin(request)
                if error:
                    return error
                store = _store_for_organization(
                    organization_id=organization_id,
                    store_id=store_id,
                )
                if store is None:
                    return _resource_unavailable()
                as_of_date, balances = get_store_balances(request=request, store_id=store.pk)
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except StoreUnavailable:
            return _resource_unavailable()
        except OperationalCalendarMissing:
            return _calendar_required()

        data = {
            "storeId": store.pk,
            "asOfOperationalDate": as_of_date,
            "balances": balances,
        }
        return Response(StockBalanceListSerializer(data).data, status=HTTP_200_OK)


class StoreDailyProductSalesView(InventoryAdministratorAPIView):
    """Read and record explicit daily totals for one store."""

    def get(self, request, store_id):
        serializer = DailyProductSaleQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)

        try:
            with transaction.atomic():
                _, error = _organization_and_admin(request)
                if error:
                    return error
                store, _opening_batch, sales = list_daily_sales(
                    request=request,
                    store_id=store_id,
                    operational_date_from=serializer.validated_data.get("operational_date_from"),
                    operational_date_to=serializer.validated_data.get("operational_date_to"),
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except OperationalCalendarMissing:
            return _calendar_required()
        except DailySaleStoreUnavailable:
            return _resource_unavailable()

        return Response(
            DailyProductSalesListSerializer(
                {
                    "storeId": store.pk,
                    "operationalDateFrom": serializer.validated_data["operational_date_from"],
                    "operationalDateTo": serializer.validated_data["operational_date_to"],
                    "sales": sales,
                }
            ).data,
            status=HTTP_200_OK,
        )

    def post(self, request, store_id):
        serializer = DailyProductSaleCreateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        try:
            with transaction.atomic():
                _, error = _organization_and_admin(request)
                if error:
                    return error
                sale, created = create_daily_sale(
                    request=request,
                    store_id=store_id,
                    product_id=values["product_id"],
                    operational_date=values["operational_date"],
                    quantity=values["quantity"],
                    operation_id=values["operation_id"],
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except OperationalCalendarMissing:
            return _calendar_required()
        except OpeningStockRequired:
            return _opening_stock_required()
        except DailySaleStoreUnavailable:
            return _resource_unavailable()
        except DailySaleProductUnavailable:
            return _resource_unavailable()
        except DailySaleProductNotAssorted:
            return _validation_response(
                {"productId": ["O produto ainda não fazia parte do sortimento da loja nesta data."]}
            )
        except DailySaleAlreadyRecorded:
            return _daily_sale_conflict()
        except IdempotencyConflict:
            return _idempotency_conflict()
        except DailySaleDateInFuture:
            return _validation_response(
                {"operationalDate": ["A data operacional não pode ser futura."]}
            )
        except InvalidDailySale:
            return _validation_response(
                {"quantity": ["A quantidade precisa respeitar a unidade-base do produto."]}
            )
        except IntegrityError as error:
            constraint = _violated_constraint(error)
            if constraint == "inv_sale_daily_unique":
                return _daily_sale_conflict()
            if constraint in {"inv_sale_op_unique", "inv_operation_org_id_unique"}:
                return _idempotency_conflict()
            raise

        return Response(
            DailyProductSaleSerializer(sale).data,
            status=HTTP_201_CREATED if created else HTTP_200_OK,
        )


class StoreDailySaleCorrectionsView(InventoryAdministratorAPIView):
    """Read a sale's correction history and make explicit audited corrections."""

    def get(self, request, store_id, daily_sale_id):
        try:
            with transaction.atomic():
                _, error = _organization_and_admin(request)
                if error:
                    return error
                sale, corrections = list_sales_corrections(
                    request=request,
                    store_id=store_id,
                    sale_id=daily_sale_id,
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except DailySaleStoreUnavailable:
            return _resource_unavailable()
        except DailySaleUnavailable:
            return _resource_unavailable()

        return Response(
            SalesCorrectionListSerializer(
                {"dailySaleId": sale.pk, "corrections": corrections}
            ).data,
            status=HTTP_200_OK,
        )

    def post(self, request, store_id, daily_sale_id):
        serializer = SalesCorrectionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _validation_response(serializer.errors)
        values = serializer.validated_data

        try:
            with transaction.atomic():
                _, error = _organization_and_admin(request)
                if error:
                    return error
                correction, created = correct_daily_sale(
                    request=request,
                    store_id=store_id,
                    sale_id=daily_sale_id,
                    expected_current_quantity=values["expected_current_quantity"],
                    new_quantity=values["new_quantity"],
                    reason=values["reason"],
                    operation_id=values["operation_id"],
                )
        except ActiveOrganizationRequired:
            return _active_organization_required()
        except OperationalCalendarMissing:
            return _calendar_required()
        except OpeningStockRequired:
            return _opening_stock_required()
        except DailySaleStoreUnavailable:
            return _resource_unavailable()
        except DailySaleUnavailable:
            return _resource_unavailable()
        except StaleDailySale:
            return _stale_daily_sale()
        except IdempotencyConflict:
            return _idempotency_conflict()
        except InvalidDailySale:
            return _validation_response(
                {"newQuantity": ["A nova quantidade deve ser diferente e respeitar a unidade-base."]}
            )
        except IntegrityError as error:
            if _violated_constraint(error) in {
                "inv_sale_correction_op_unique",
                "inv_operation_org_id_unique",
            }:
                return _idempotency_conflict()
            raise

        return Response(
            SalesCorrectionSerializer(correction).data,
            status=HTTP_201_CREATED if created else HTTP_200_OK,
        )
