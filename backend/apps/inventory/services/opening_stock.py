"""Atomic opening-stock capture and movement-derived balances."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.inventory.models import (
    OpeningStockBatch,
    OpeningStockLine,
    Product,
    StockMovement,
    Store,
    StoreProduct,
)
from apps.inventory.services.calendar import (
    OperationalCalendarMissing,
    current_operational_date,
    resolve_operational_calendar,
)


class StoreUnavailable(Exception):
    """Store is missing or belongs to another organization."""


class ProductUnavailable(Exception):
    """Product is missing or belongs to another organization."""


class ProductNotAssorted(Exception):
    """Product is not in the store assortment on the counted date."""


class OpeningStockAlreadyRecorded(Exception):
    """An initial count has already been recorded for this store."""


class InvalidOpeningStock(Exception):
    """Opening-stock quantities or line identities are invalid."""


class OpeningDateInFuture(Exception):
    """The physical count cannot close a date after the current store date."""


def _validate_quantity(*, quantity: Decimal, unit: str) -> None:
    if not quantity.is_finite() or quantity < 0:
        raise InvalidOpeningStock
    if unit == Product.Unit.UNIT and quantity != quantity.to_integral_value():
        raise InvalidOpeningStock
    if unit in (Product.Unit.KILOGRAM, Product.Unit.LITER) and quantity.as_tuple().exponent < -3:
        raise InvalidOpeningStock


def _store_for_org(*, organization_id, store_id, lock=False):
    stores = Store.objects.filter(organization_id=organization_id)
    if lock:
        stores = stores.select_for_update()
    try:
        return stores.get(pk=store_id, is_active=True)
    except Store.DoesNotExist as exc:
        raise StoreUnavailable from exc


def confirm_opening_stock(*, request, store_id, closed_operational_date: date, lines: list[dict]):
    """Validate and record a complete opening count atomically for one store."""
    if not lines:
        raise InvalidOpeningStock

    product_ids = [line["product_id"] for line in lines]
    if len(product_ids) != len(set(product_ids)):
        raise InvalidOpeningStock

    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_org(
            organization_id=organization_id,
            store_id=store_id,
            lock=True,
        )

        current_date, _ = current_operational_date(
            organization_id=organization_id,
            store=store,
        )
        if closed_operational_date > current_date:
            raise OpeningDateInFuture

        try:
            resolve_operational_calendar(
                organization_id=organization_id,
                operational_date=closed_operational_date,
                store=store,
            )
        except OperationalCalendarMissing:
            raise

        if OpeningStockBatch.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
        ).exists():
            raise OpeningStockAlreadyRecorded

        products = {
            product.id: product
            for product in Product.objects.filter(
                organization_id=organization_id,
                id__in=product_ids,
                is_active=True,
            )
        }
        if len(products) != len(product_ids):
            raise ProductUnavailable

        expected_product_ids = set(
            StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                product__is_active=True,
                effective_from__lte=closed_operational_date,
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=closed_operational_date))
            .values_list("product_id", flat=True)
        )
        if set(product_ids) != expected_product_ids:
            raise InvalidOpeningStock

        quantities = []
        for line in lines:
            product = products[line["product_id"]]
            quantity = line["quantity"]
            _validate_quantity(quantity=quantity, unit=product.unit)
            is_assorted = StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                product_id=product.pk,
                effective_from__lte=closed_operational_date,
            ).filter(
                Q(effective_to__isnull=True)
                | Q(effective_to__gt=closed_operational_date)
            ).exists()
            if not is_assorted:
                raise ProductNotAssorted
            quantities.append((product, quantity))

        cutoff_date = closed_operational_date + timedelta(days=1)
        batch = OpeningStockBatch.objects.create(
            organization_id=organization_id,
            store=store,
            closed_operational_date=closed_operational_date,
            cutoff_date=cutoff_date,
            source=OpeningStockBatch.Source.MANUAL,
            created_by=request.user,
        )

        created_lines = []
        for product, quantity in quantities:
            line = OpeningStockLine.objects.create(
                organization_id=organization_id,
                batch=batch,
                store=store,
                product=product,
                quantity=quantity,
            )
            StockMovement.objects.create(
                organization_id=organization_id,
                store=store,
                product=product,
                kind=StockMovement.Kind.OPENING,
                operational_date=cutoff_date,
                quantity_delta=quantity,
                opening_line=line,
                created_by=request.user,
            )
            created_lines.append(line)

    return batch, created_lines


def get_store_balances(*, request, store_id):
    """Return each known store product with its current ledger-derived balance."""
    with transaction.atomic():
        organization_id = set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        )
        store = _store_for_org(organization_id=organization_id, store_id=store_id)
        operational_date, _ = current_operational_date(
            organization_id=organization_id,
            store=store,
        )
        try:
            resolve_operational_calendar(
                organization_id=organization_id,
                operational_date=operational_date,
                store=store,
            )
        except OperationalCalendarMissing:
            raise

        product_ids = set(
            StoreProduct.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).values_list("product_id", flat=True)
        )
        movement_balances = {
            row["product_id"]: row["balance"]
            for row in StockMovement.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            )
            .values("product_id")
            .annotate(balance=Sum("quantity_delta"))
        }
        product_ids.update(movement_balances)
        products = Product.objects.filter(
            organization_id=organization_id,
            id__in=product_ids,
        ).order_by("sku", "id")
        balances = [
            {
                "product": product,
                "quantity": movement_balances.get(product.id, Decimal("0")),
            }
            for product in products
        ]
        return operational_date, balances
