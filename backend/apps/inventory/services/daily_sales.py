"""Transactional daily-sales recording, correction, and ledger effects."""

import hashlib
import json
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q

from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.inventory.models import (
    DailyProductSale,
    InventoryOperation,
    OpeningStockBatch,
    Product,
    SalesCorrection,
    StockMovement,
    Store,
    StoreProduct,
)
from apps.inventory.services.calendar import (
    current_operational_date,
    resolve_operational_calendar,
)


class StoreUnavailable(Exception):
    """Store is missing or belongs to another organization."""


class DailySaleUnavailable(Exception):
    """Daily sale is missing or belongs to another organization/store."""


class ProductUnavailable(Exception):
    """Product is missing or inactive in this organization."""


class ProductNotAssorted(Exception):
    """Product has no assortment link that began on or before the sale date."""


class OpeningStockRequired(Exception):
    """The store needs an opening count before a sales date can be classified."""


class DailySaleAlreadyRecorded(Exception):
    """A total already exists for this product/store/operational-date key."""


class IdempotencyConflict(Exception):
    """An operation ID was reused with a different payload."""


class StaleDailySale(Exception):
    """A correction is based on a total that has since changed."""


class InvalidDailySale(Exception):
    """Quantity is invalid for the product unit or is an unchanged correction."""


class DailySaleDateInFuture(Exception):
    """Daily sales cannot be recorded for a future operational date."""


def _store_for_organization(*, organization_id, store_id, lock=False):
    stores = Store.objects.filter(organization_id=organization_id)
    if lock:
        stores = stores.select_for_update()
    try:
        return stores.get(pk=store_id, is_active=True)
    except Store.DoesNotExist as exc:
        raise StoreUnavailable from exc


def _validate_quantity(*, quantity: Decimal, unit: str):
    if not quantity.is_finite() or quantity < 0:
        raise InvalidDailySale
    if unit == Product.Unit.UNIT and quantity != quantity.to_integral_value():
        raise InvalidDailySale
    if unit in (Product.Unit.KILOGRAM, Product.Unit.LITER) and quantity.as_tuple().exponent < -3:
        raise InvalidDailySale


def _validate_operational_date(*, organization_id, store, operational_date):
    current_date, _ = current_operational_date(
        organization_id=organization_id,
        store=store,
    )
    resolve_operational_calendar(
        organization_id=organization_id,
        operational_date=operational_date,
        store=store,
    )
    if operational_date > current_date:
        raise DailySaleDateInFuture

    opening_batch = (
        OpeningStockBatch.objects.select_for_update()
        .filter(organization_id=organization_id, store_id=store.pk)
        .first()
    )
    if opening_batch is None:
        raise OpeningStockRequired
    return opening_batch


def _quantity_token(quantity: Decimal) -> str:
    if quantity == 0:
        return "0"
    return format(quantity.normalize(), "f")


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _existing_operation(*, organization_id, operation_id, operation_type, payload_hash):
    operation = InventoryOperation.objects.filter(
        organization_id=organization_id,
        operation_id=operation_id,
    ).first()
    if operation is None:
        return None
    if operation.operation_type != operation_type or operation.payload_hash != payload_hash:
        raise IdempotencyConflict
    return operation


def _record_operation(
    *, organization_id, operation_id, operation_type, payload_hash, resource_id, actor
):
    return InventoryOperation.objects.create(
        organization_id=organization_id,
        operation_id=operation_id,
        operation_type=operation_type,
        payload_hash=payload_hash,
        resource_id=resource_id,
        created_by=actor,
    )


def _decorate_sale(sale, *, cutoff_date, demand_eligible, movement_id=None):
    sale.cutoff_date = cutoff_date
    sale.demand_eligible = demand_eligible
    sale.stock_treatment = (
        "demand_only" if sale.operational_date < cutoff_date else "outbound_movement"
    )
    sale.initial_stock_movement_id = movement_id
    return sale


def list_daily_sales(
    *,
    request,
    store_id,
    operational_date_from: date | None = None,
    operational_date_to: date | None = None,
):
    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_organization(organization_id=organization_id, store_id=store_id)
        resolve_operational_calendar(
            organization_id=organization_id,
            operational_date=operational_date_from,
            store=store,
        )
        resolve_operational_calendar(
            organization_id=organization_id,
            operational_date=operational_date_to,
            store=store,
        )
        opening_batch = OpeningStockBatch.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
        ).first()
        if opening_batch is None:
            return store, None, []

        sales_query = DailyProductSale.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
        ).select_related("product", "created_by").annotate(
            correction_count=Count("corrections", distinct=True)
        )
        if operational_date_from is not None:
            sales_query = sales_query.filter(operational_date__gte=operational_date_from)
        if operational_date_to is not None:
            sales_query = sales_query.filter(operational_date__lte=operational_date_to)
        sales = list(sales_query)

        product_ids = {sale.product_id for sale in sales}
        assortments = list(
            StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                product_id__in=product_ids,
            ).values("product_id", "effective_from", "effective_to")
        )
        movement_ids = dict(
            StockMovement.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                daily_sale_id__in=[sale.pk for sale in sales],
            ).values_list("daily_sale_id", "id")
        )

        by_product = {}
        for assortment in assortments:
            by_product.setdefault(assortment["product_id"], []).append(assortment)

        for sale in sales:
            demand_eligible = any(
                assortment["effective_from"] <= sale.operational_date
                and (
                    assortment["effective_to"] is None
                    or sale.operational_date < assortment["effective_to"]
                )
                for assortment in by_product.get(sale.product_id, [])
            )
            _decorate_sale(
                sale,
                cutoff_date=opening_batch.cutoff_date,
                demand_eligible=demand_eligible,
                movement_id=movement_ids.get(sale.pk),
            )

        return store, opening_batch, sales


def create_daily_sale(
    *, request, store_id, product_id, operational_date, quantity, operation_id,
    source=DailyProductSale.Source.MANUAL,
):
    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_organization(
            organization_id=organization_id,
            store_id=store_id,
            lock=True,
        )

        opening_batch = _validate_operational_date(
            organization_id=organization_id,
            store=store,
            operational_date=operational_date,
        )

        payload_hash = _payload_hash(
            {
                "storeId": str(store.pk),
                "productId": str(product_id),
                "operationalDate": operational_date.isoformat(),
                "quantity": _quantity_token(quantity),
            }
        )
        previous_operation = _existing_operation(
            organization_id=organization_id,
            operation_id=operation_id,
            operation_type=InventoryOperation.Type.DAILY_SALE,
            payload_hash=payload_hash,
        )
        if previous_operation is not None:
            sale = DailyProductSale.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                pk=previous_operation.resource_id,
            ).select_related("product").annotate(
                correction_count=Count("corrections", distinct=True)
            ).first()
            if sale is None:
                raise IdempotencyConflict
            sale = _decorate_sale(
                sale,
                cutoff_date=opening_batch.cutoff_date,
                demand_eligible=StoreProduct.objects.filter(
                    organization_id=organization_id,
                    store_id=store.pk,
                    product_id=sale.product_id,
                    effective_from__lte=sale.operational_date,
                ).filter(
                    Q(effective_to__isnull=True)
                    | Q(effective_to__gt=sale.operational_date)
                ).exists(),
                movement_id=StockMovement.objects.filter(
                    organization_id=organization_id,
                    daily_sale_id=sale.pk,
                ).values_list("id", flat=True).first(),
            )
            return sale, False

        try:
            product = Product.objects.get(
                organization_id=organization_id,
                pk=product_id,
                is_active=True,
            )
        except Product.DoesNotExist as exc:
            raise ProductUnavailable from exc

        has_assortment_history = StoreProduct.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id=product.pk,
            effective_from__lte=operational_date,
        ).exists()
        # A closed assortment may still have residual stock to liquidate.
        if not has_assortment_history:
            raise ProductNotAssorted
        _validate_quantity(quantity=quantity, unit=product.unit)

        if DailyProductSale.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id=product.pk,
            operational_date=operational_date,
        ).exists():
            raise DailySaleAlreadyRecorded

        sale = DailyProductSale.objects.create(
            organization_id=organization_id,
            store=store,
            product=product,
            operational_date=operational_date,
            quantity=quantity,
            source=source,
            operation_id=operation_id,
            created_by=request.user,
        )
        movement_id = None
        if operational_date >= opening_batch.cutoff_date:
            movement = StockMovement.objects.create(
                organization_id=organization_id,
                store=store,
                product=product,
                kind=StockMovement.Kind.SALE,
                operational_date=operational_date,
                quantity_delta=-quantity,
                daily_sale=sale,
                created_by=request.user,
            )
            movement_id = movement.pk

        _record_operation(
            organization_id=organization_id,
            operation_id=operation_id,
            operation_type=InventoryOperation.Type.DAILY_SALE,
            payload_hash=payload_hash,
            resource_id=sale.pk,
            actor=request.user,
        )

        demand_eligible = StoreProduct.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id=product.pk,
            effective_from__lte=operational_date,
        ).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gt=operational_date)
        ).exists()
        sale.correction_count = 0
        return _decorate_sale(
            sale,
            cutoff_date=opening_batch.cutoff_date,
            demand_eligible=demand_eligible,
            movement_id=movement_id,
        ), True


def list_sales_corrections(*, request, store_id, sale_id):
    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_organization(organization_id=organization_id, store_id=store_id)
        try:
            sale = DailyProductSale.objects.get(
                organization_id=organization_id,
                store_id=store.pk,
                pk=sale_id,
            )
        except DailyProductSale.DoesNotExist as exc:
            raise DailySaleUnavailable from exc

        corrections = list(
            SalesCorrection.objects.filter(
                organization_id=organization_id,
                sale_id=sale.pk,
            ).select_related("created_by").order_by("created_at", "id")
        )
        movement_ids = dict(
            StockMovement.objects.filter(
                organization_id=organization_id,
                sale_correction_id__in=[correction.pk for correction in corrections],
            ).values_list("sale_correction_id", "id")
        )
        for correction in corrections:
            correction.inventory_movement_id = movement_ids.get(correction.pk)
        return sale, corrections


def correct_daily_sale(
    *,
    request,
    store_id,
    sale_id,
    expected_current_quantity,
    new_quantity,
    reason,
    operation_id,
):
    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_organization(
            organization_id=organization_id,
            store_id=store_id,
            lock=True,
        )

        payload_hash = _payload_hash(
            {
                "storeId": str(store.pk),
                "dailySaleId": str(sale_id),
                "expectedCurrentQuantity": _quantity_token(expected_current_quantity),
                "newQuantity": _quantity_token(new_quantity),
                "reason": reason,
            }
        )
        previous_operation = _existing_operation(
            organization_id=organization_id,
            operation_id=operation_id,
            operation_type=InventoryOperation.Type.SALES_CORRECTION,
            payload_hash=payload_hash,
        )
        if previous_operation is not None:
            correction = SalesCorrection.objects.filter(
                organization_id=organization_id,
                sale__store_id=store.pk,
                sale_id=sale_id,
                pk=previous_operation.resource_id,
            ).first()
            if correction is None:
                raise IdempotencyConflict
            correction.inventory_movement_id = StockMovement.objects.filter(
                organization_id=organization_id,
                sale_correction_id=correction.pk,
            ).values_list("id", flat=True).first()
            return correction, False

        try:
            sale = DailyProductSale.objects.select_for_update().select_related("product").get(
                organization_id=organization_id,
                store_id=store.pk,
                pk=sale_id,
            )
        except DailyProductSale.DoesNotExist as exc:
            raise DailySaleUnavailable from exc

        opening_batch = _validate_operational_date(
            organization_id=organization_id,
            store=store,
            operational_date=sale.operational_date,
        )
        if sale.quantity != expected_current_quantity:
            raise StaleDailySale
        _validate_quantity(quantity=new_quantity, unit=sale.product.unit)
        if new_quantity == sale.quantity:
            raise InvalidDailySale

        correction = SalesCorrection.objects.create(
            organization_id=organization_id,
            sale=sale,
            previous_quantity=sale.quantity,
            new_quantity=new_quantity,
            reason=reason,
            operation_id=operation_id,
            created_by=request.user,
        )
        previous_quantity = sale.quantity
        sale.quantity = new_quantity
        sale.save(update_fields=["quantity", "updated_at"])

        if sale.operational_date >= opening_batch.cutoff_date:
            movement = StockMovement.objects.create(
                organization_id=organization_id,
                store=store,
                product=sale.product,
                kind=StockMovement.Kind.SALE_CORRECTION,
                operational_date=sale.operational_date,
                quantity_delta=previous_quantity - new_quantity,
                sale_correction=correction,
                created_by=request.user,
            )
            correction.inventory_movement_id = movement.pk
        else:
            correction.inventory_movement_id = None

        _record_operation(
            organization_id=organization_id,
            operation_id=operation_id,
            operation_type=InventoryOperation.Type.SALES_CORRECTION,
            payload_hash=payload_hash,
            resource_id=correction.pk,
            actor=request.user,
        )

        return correction, True
