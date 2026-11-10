"""Transactional supplier and purchasing-condition operations."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.inventory.models import Product, Store
from apps.catalog.models import Supplier, SupplierProductCondition


class SupplierUnavailable(Exception):
    """Supplier is missing or belongs to another organization."""


class ProductUnavailable(Exception):
    """Product is missing, inactive, or belongs to another organization."""


class StoreUnavailable(Exception):
    """Store is missing, inactive, or belongs to another organization."""


class SupplierConditionDateConflict(Exception):
    """Condition versions must be added after the latest version in their scope."""


class InvalidSupplierCondition(Exception):
    """The condition does not match the base unit's quantity rules."""


def _organization_for_request(request):
    return set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )


def list_suppliers(*, request):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        return list(Supplier.objects.filter(organization_id=organization_id).select_related(
            "created_by", "updated_by"
        ))


def get_supplier(*, request, supplier_id):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        try:
            return Supplier.objects.select_related("created_by", "updated_by").get(
                organization_id=organization_id,
                pk=supplier_id,
            )
        except Supplier.DoesNotExist as exc:
            raise SupplierUnavailable from exc


def create_supplier(*, request, values):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        return Supplier.objects.create(
            organization_id=organization_id,
            created_by=request.user,
            updated_by=request.user,
            **values,
        )


def update_supplier(*, request, supplier_id, values):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        try:
            supplier = Supplier.objects.select_for_update().get(
                organization_id=organization_id,
                pk=supplier_id,
            )
        except Supplier.DoesNotExist as exc:
            raise SupplierUnavailable from exc

        for field, value in values.items():
            setattr(supplier, field, value)
        supplier.updated_by = request.user
        supplier.save(update_fields=[*values.keys(), "updated_by", "updated_at"])
        return Supplier.objects.select_related("created_by", "updated_by").get(pk=supplier.pk)


def _get_active_supplier(*, organization_id, supplier_id, lock=False):
    query = Supplier.objects.filter(organization_id=organization_id, is_active=True)
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=supplier_id)
    except Supplier.DoesNotExist as exc:
        raise SupplierUnavailable from exc


def _get_active_product(*, organization_id, product_id, lock=False):
    query = Product.objects.filter(organization_id=organization_id, is_active=True)
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=product_id)
    except Product.DoesNotExist as exc:
        raise ProductUnavailable from exc


def _get_active_store(*, organization_id, store_id, lock=False):
    query = Store.objects.filter(organization_id=organization_id, is_active=True)
    if lock:
        query = query.select_for_update()
    try:
        return query.get(pk=store_id)
    except Store.DoesNotExist as exc:
        raise StoreUnavailable from exc


def list_supplier_conditions(*, request, supplier_id, product_id=None, store_id=None):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        try:
            supplier = Supplier.objects.get(organization_id=organization_id, pk=supplier_id)
        except Supplier.DoesNotExist as exc:
            raise SupplierUnavailable from exc

        conditions = SupplierProductCondition.objects.filter(
            organization_id=organization_id,
            supplier_id=supplier.pk,
        ).select_related("supplier", "product", "store", "created_by")

        if product_id is not None:
            _get_active_product(organization_id=organization_id, product_id=product_id)
            conditions = conditions.filter(product_id=product_id)

        if store_id is not None:
            store = _get_active_store(organization_id=organization_id, store_id=store_id)
            conditions = conditions.filter(Q(store__isnull=True) | Q(store_id=store.pk))

        return supplier, list(conditions)


def _validate_quantity_for_unit(*, product, values):
    if product.unit != Product.Unit.UNIT:
        return
    purchase_quantities = (
        values["base_units_per_purchase_unit"],
        values["minimum_purchase_quantity"],
        values["purchase_multiple"],
    )
    if any(quantity != quantity.to_integral_value() for quantity in purchase_quantities):
        raise InvalidSupplierCondition


def create_supplier_condition(*, request, supplier_id, values):
    with transaction.atomic():
        organization_id = _organization_for_request(request)
        supplier = _get_active_supplier(
            organization_id=organization_id,
            supplier_id=supplier_id,
            lock=True,
        )
        product = _get_active_product(
            organization_id=organization_id,
            product_id=values["product_id"],
            lock=True,
        )
        store = None
        store_id = values.get("store_id")
        if store_id is not None:
            store = _get_active_store(
                organization_id=organization_id,
                store_id=store_id,
                lock=True,
            )

        _validate_quantity_for_unit(product=product, values=values)

        previous = SupplierProductCondition.objects.filter(
            organization_id=organization_id,
            supplier_id=supplier.pk,
            product_id=product.pk,
            store_id=store.pk if store is not None else None,
        ).order_by("-effective_from", "-id").first()
        effective_from = values["effective_from"]
        if previous and effective_from <= previous.effective_from:
            raise SupplierConditionDateConflict

        condition = SupplierProductCondition.objects.create(
            organization_id=organization_id,
            supplier=supplier,
            product=product,
            store=store,
            created_by=request.user,
            **{key: value for key, value in values.items() if key != "store_id"},
        )
        return SupplierProductCondition.objects.select_related(
            "supplier", "product", "store", "created_by"
        ).get(pk=condition.pk)
