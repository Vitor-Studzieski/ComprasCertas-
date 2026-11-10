"""Transactional per-store purchase order, budget, and receipt operations."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.catalog.models import SupplierProductCondition
from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.inventory.models import Product, StockMovement, Store
from apps.inventory.services.calendar import OperationalCalendarMissing, current_operational_date
from apps.inventory.services.replenishment import generate_store_recommendations
from apps.purchasing.models import (
    PurchaseBudget,
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderItem,
    PurchaseReceipt,
    PurchaseReceiptLine,
)


MONEY_QUANTUM = Decimal("0.01")
ACTIVE_COMMITMENT_STATUSES = [
    PurchaseOrder.Status.APPROVED,
    PurchaseOrder.Status.PARTIALLY_RECEIVED,
    PurchaseOrder.Status.RECEIVED,
    PurchaseOrder.Status.CANCELLED,
]


class PurchaseUnavailable(Exception):
    pass


class PurchaseConflict(Exception):
    pass


class PurchaseBudgetRequired(Exception):
    pass


class PurchaseBudgetExceeded(Exception):
    def __init__(self, *, weekly_limit, weekly_after, monthly_limit, monthly_after):
        self.weekly_limit = weekly_limit
        self.weekly_after = weekly_after
        self.monthly_limit = monthly_limit
        self.monthly_after = monthly_after


class PurchaseValidationError(Exception):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f") if value else "0"


def _money_text(value: Decimal | None) -> str | None:
    return format(_money(value), "f") if value is not None else None


def _get_store(*, organization_id, store_id, lock=False):
    stores = Store.objects.filter(organization_id=organization_id, is_active=True)
    if lock:
        stores = stores.select_for_update()
    store = stores.filter(pk=store_id).first()
    if store is None:
        raise PurchaseUnavailable
    return store


def _recommendations(*, organization_id, store):
    try:
        as_of_date, _ = current_operational_date(organization_id=organization_id, store=store)
    except OperationalCalendarMissing:
        raise
    response = generate_store_recommendations(
        organization_id=organization_id,
        store=store,
        as_of_date=as_of_date,
    )
    return as_of_date, {
        row["product"]["id"]: {**row, "asOfDate": response["asOfDate"]}
        for row in response["recommendations"]
    }, response


def _condition_from_comparison(*, organization_id, store, product, comparison):
    condition = SupplierProductCondition.objects.filter(
        organization_id=organization_id,
        pk=comparison["supplierConditionId"],
        supplier_id=comparison["supplierId"],
        product_id=product.pk,
    ).filter(Q(store_id=store.pk) | Q(store__isnull=True)).first()
    if condition is None:
        raise PurchaseUnavailable
    return condition


def _line_snapshot(recommendation, comparison):
    keys = (
        "currentStock",
        "averageDailyDemand",
        "validDemandDays",
        "targetStock",
        "deliveryLeadTimeDays",
        "stockAtArrival",
        "predictedShortageBeforeArrival",
        "quantityNeededBase",
        "orderQuantity",
        "unitCost",
        "baseUnitsPerPurchaseUnit",
        "minimumPurchaseQuantity",
        "purchaseMultiple",
        "supplierConditionId",
        "unitCostSource",
        "knownMerchandiseCost",
        "selected",
    )
    return {
        "asOfDate": recommendation["asOfDate"],
        "window": recommendation["window"],
        "ruleVersion": recommendation["ruleVersion"],
        "values": {key: comparison.get(key) for key in keys if key in comparison}
        | {
            "currentStock": recommendation["currentStock"],
            "averageDailyDemand": recommendation["averageDailyDemand"],
            "validDemandDays": recommendation["validDemandDays"],
            "targetStock": recommendation["targetStock"],
            "selectedSupplierId": recommendation["selectedSupplierId"],
        },
    }


def _create_item(*, organization_id, order, product, comparison, recommendation, quantity):
    condition = _condition_from_comparison(
        organization_id=organization_id,
        store=order.store,
        product=product,
        comparison=comparison,
    )
    return PurchaseOrderItem.objects.create(
        organization_id=organization_id,
        order=order,
        product=product,
        supplier_condition=condition,
        purchase_unit_name=comparison["purchaseUnitName"],
        base_units_per_purchase_unit=Decimal(comparison["baseUnitsPerPurchaseUnit"]),
        unit_price=Decimal(comparison["unitCost"]),
        minimum_purchase_quantity=Decimal(comparison["minimumPurchaseQuantity"]),
        purchase_multiple=Decimal(comparison["purchaseMultiple"]),
        recommended_quantity=Decimal(comparison["orderQuantity"] or "0"),
        quantity=quantity,
        source_snapshot=_line_snapshot(recommendation, comparison),
    )


def _record_event(*, organization_id, order, actor, event_type, payload=None, reason=""):
    PurchaseOrderEvent.objects.create(
        organization_id=organization_id,
        order=order,
        actor=actor,
        event_type=event_type,
        payload=payload or {},
        reason=reason,
    )


def _order_totals(order):
    total = sum((item.quantity * item.unit_price for item in order.items.all()), Decimal("0"))
    return _money(total)


def _item_repr(item):
    approved = item.approved_quantity
    return {
        "productId": str(item.product_id),
        "sku": item.product.sku,
        "productName": item.product.name,
        "purchaseUnitName": item.purchase_unit_name,
        "baseUnitsPerPurchaseUnit": _decimal_text(item.base_units_per_purchase_unit),
        "recommendedQuantity": _decimal_text(item.recommended_quantity),
        "quantity": _decimal_text(item.quantity),
        "approvedQuantity": _decimal_text(approved),
        "receivedQuantity": _decimal_text(item.received_quantity),
        "unitPrice": _money_text(item.unit_price),
        "estimatedLineTotal": _money_text(item.quantity * item.unit_price),
        "minimumPurchaseQuantity": _decimal_text(item.minimum_purchase_quantity),
        "purchaseMultiple": _decimal_text(item.purchase_multiple),
        "sourceSnapshot": item.source_snapshot,
    }


def _order_repr(order):
    source = order.source_snapshot or {}
    if order.approved_at is None:
        budget_status = "not_approved"
    elif order.budget_exception_reason:
        budget_status = "exception_approved"
    else:
        budget_status = "within_limits"
    items = [_item_repr(item) for item in order.items.select_related("product").all()]
    return {
        "id": str(order.pk),
        "storeId": str(order.store_id),
        "supplierId": str(order.supplier_id),
        "supplierName": order.supplier.name,
        "status": order.status,
        "sourceAsOfDate": source.get("asOfDate"),
        "estimatedTotal": _money_text(order.estimated_total),
        "committedAmount": _money_text(order.committed_amount),
        "freightStatus": "unknown",
        "costCompleteness": "partial",
        "budgetWeekStart": order.budget_week_start.isoformat() if order.budget_week_start else None,
        "budgetMonthStart": order.budget_month_start.isoformat() if order.budget_month_start else None,
        "budgetStatus": budget_status,
        "budgetExceptionReason": order.budget_exception_reason,
        "stale": order.status == PurchaseOrder.Status.STALE,
        "createdAt": order.created_at.isoformat(),
        "approvedAt": order.approved_at.isoformat() if order.approved_at else None,
        "items": items,
    }


def _receipt_repr(receipt):
    lines = []
    for line in receipt.lines.select_related("order_item__product").all():
        lines.append({
            "productId": str(line.order_item.product_id),
            "sku": line.order_item.product.sku,
            "productName": line.order_item.product.name,
            "physicalQuantity": _decimal_text(line.physical_quantity),
            "acceptedQuantity": _decimal_text(line.accepted_quantity),
            "pendingExcessQuantity": _decimal_text(line.pending_excess_quantity),
            "actualUnitPrice": _money_text(line.actual_unit_price),
            "lineTotal": _money_text(line.accepted_quantity * line.actual_unit_price),
        })
    return {
        "id": str(receipt.pk),
        "receivedAt": receipt.received_at.isoformat(),
        "operationalDate": receipt.operational_date.isoformat(),
        "supplierReference": receipt.supplier_reference,
        "budgetExceptionReason": receipt.budget_exception_reason,
        "lines": lines,
    }


def list_store_orders(*, organization_id, store_id):
    store = _get_store(organization_id=organization_id, store_id=store_id)
    rows = PurchaseOrder.objects.filter(
        organization_id=organization_id,
        store_id=store.pk,
    ).select_related("store", "supplier").prefetch_related("items__product")
    return {"purchaseOrders": [_order_repr(order) for order in rows]}


def get_purchase_order(*, organization_id, store_id, order_id, lock=False):
    orders = PurchaseOrder.objects.filter(
        organization_id=organization_id,
        store_id=store_id,
        pk=order_id,
    )
    if lock:
        orders = orders.select_for_update()
    order = orders.select_related("store", "supplier").prefetch_related("items__product").first()
    if order is None:
        raise PurchaseUnavailable
    return order


@transaction.atomic
def create_purchase_order_draft(*, request, store_id, supplier_id, items):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
    supplier_conditions = SupplierProductCondition.objects.filter(
        organization_id=organization_id,
        supplier_id=supplier_id,
    )
    if not supplier_conditions.exists():
        raise PurchaseUnavailable
    supplier = supplier_conditions.first().supplier
    as_of_date, recommendations, snapshot = _recommendations(
        organization_id=organization_id,
        store=store,
    )
    product_ids = [row["productId"] for row in items]
    products = {
        product.pk: product
        for product in Product.objects.filter(
            organization_id=organization_id,
            pk__in=product_ids,
            is_active=True,
        )
    }
    if len(products) != len(product_ids):
        raise PurchaseUnavailable

    order = PurchaseOrder.objects.create(
        organization_id=organization_id,
        store=store,
        supplier=supplier,
        status=PurchaseOrder.Status.DRAFT,
        source_snapshot={
            "asOfDate": as_of_date.isoformat(),
            "generatedAt": snapshot["generatedAt"],
            "ruleVersion": snapshot["ruleVersion"],
        },
        created_by=request.user,
        updated_by=request.user,
    )
    for product_id in product_ids:
        recommendation = recommendations.get(str(product_id))
        if recommendation is None or recommendation["selectedSupplierId"] != str(supplier.pk):
            raise PurchaseValidationError("O fornecedor precisa ser o recomendado para todos os itens do rascunho.")
        comparison = next(
            (row for row in recommendation["supplierComparisons"] if row["supplierId"] == str(supplier.pk)),
            None,
        )
        if comparison is None or comparison["orderQuantity"] is None or Decimal(comparison["orderQuantity"]) <= 0:
            raise PurchaseValidationError("Um ou mais itens ainda não têm quantidade recomendada para esse fornecedor.")
        _create_item(
            organization_id=organization_id,
            order=order,
            product=products[product_id],
            comparison=comparison,
            recommendation=recommendation,
            quantity=Decimal(comparison["orderQuantity"]),
        )
    order.estimated_total = _order_totals(order)
    order.save(update_fields=["estimated_total", "updated_at"])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="draft_created",
        payload={"supplierId": str(supplier.pk), "productIds": [str(value) for value in product_ids]},
    )
    return _order_repr(order)


def _snapshot_is_current(*, organization_id, order):
    _, recommendations, _ = _recommendations(
        organization_id=organization_id,
        store=order.store,
    )
    for item in order.items.select_related("product").all():
        recommendation = recommendations.get(str(item.product_id))
        if recommendation is None:
            return False
        comparison = next(
            (
                row for row in recommendation["supplierComparisons"]
                if row["supplierId"] == str(order.supplier_id)
            ),
            None,
        )
        if comparison is None:
            return False
        if item.source_snapshot != _line_snapshot(recommendation, comparison):
            return False
    return True


def _mark_stale(*, organization_id, order, actor):
    order.status = PurchaseOrder.Status.STALE
    order.updated_by = actor
    order.save(update_fields=["status", "updated_by", "updated_at"])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=actor,
        event_type="marked_stale",
        payload={"sourceAsOfDate": (order.source_snapshot or {}).get("asOfDate")},
    )


@transaction.atomic
def edit_purchase_order_draft(*, request, store_id, order_id, supplier_id, items, change_reason=""):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    order = get_purchase_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise PurchaseConflict
    store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
    as_of_date, recommendations, snapshot = _recommendations(
        organization_id=organization_id,
        store=store,
    )
    product_ids = [row["productId"] for row in items]
    products = {
        product.pk: product
        for product in Product.objects.filter(
            organization_id=organization_id,
            pk__in=product_ids,
            is_active=True,
        )
    }
    if len(products) != len(product_ids):
        raise PurchaseUnavailable
    supplier = SupplierProductCondition.objects.filter(
        organization_id=organization_id,
        supplier_id=supplier_id,
    ).first()
    if supplier is None:
        raise PurchaseUnavailable
    supplier = supplier.supplier

    change_needed = False
    prepared = []
    for row in items:
        recommendation = recommendations.get(str(row["productId"]))
        if recommendation is None:
            raise PurchaseValidationError("O produto não está ativo no sortimento desta loja.")
        comparison = next(
            (value for value in recommendation["supplierComparisons"] if value["supplierId"] == str(supplier.pk)),
            None,
        )
        if comparison is None:
            raise PurchaseValidationError("O fornecedor não tem condição válida para um dos produtos.")
        quantity = row["quantity"]
        multiple = Decimal(comparison["purchaseMultiple"])
        minimum = Decimal(comparison["minimumPurchaseQuantity"])
        if quantity <= 0 or quantity < minimum or (quantity / multiple) != (quantity / multiple).to_integral_value():
            raise PurchaseValidationError("A quantidade precisa respeitar o pedido mínimo e os múltiplos da embalagem.")
        recommended = Decimal(comparison["orderQuantity"]) if comparison["orderQuantity"] is not None else None
        need = Decimal(comparison["quantityNeededBase"]) if comparison["quantityNeededBase"] is not None else None
        needed_purchase_units = need / Decimal(comparison["baseUnitsPerPurchaseUnit"]) if need is not None else None
        risky_supplier = comparison["predictedShortageBeforeArrival"] not in (None, "0")
        if (
            recommendation["selectedSupplierId"] != str(supplier.pk)
            or (recommended is not None and quantity != recommended)
            or (needed_purchase_units is not None and quantity < needed_purchase_units)
            or risky_supplier
        ):
            change_needed = True
        prepared.append((products[row["productId"]], comparison, recommendation, quantity))
    if change_needed and not change_reason.strip():
        raise PurchaseValidationError("Informe o motivo da exceção às regras de reposição.")

    before = [
        {"productId": str(item.product_id), "quantity": _decimal_text(item.quantity), "supplierId": str(order.supplier_id)}
        for item in order.items.all()
    ]
    order.items.all().delete()
    order.supplier = supplier
    order.source_snapshot = {
        "asOfDate": as_of_date.isoformat(),
        "generatedAt": snapshot["generatedAt"],
        "ruleVersion": snapshot["ruleVersion"],
    }
    order.updated_by = request.user
    order.save(update_fields=["supplier", "source_snapshot", "updated_by", "updated_at"])
    for product, comparison, recommendation, quantity in prepared:
        _create_item(
            organization_id=organization_id,
            order=order,
            product=product,
            comparison=comparison,
            recommendation=recommendation,
            quantity=quantity,
        )
    order.estimated_total = _order_totals(order)
    order.save(update_fields=["estimated_total", "updated_at"])
    after = [
        {"productId": str(item.product_id), "quantity": _decimal_text(item.quantity), "supplierId": str(supplier.pk)}
        for item in order.items.all()
    ]
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="draft_edited",
        payload={"before": before, "after": after},
        reason=change_reason.strip(),
    )
    return _order_repr(order)


def _effective_budget(*, organization_id, store, lock=False):
    budgets = PurchaseBudget.objects.filter(organization_id=organization_id)
    if lock:
        budgets = budgets.select_for_update()
    network = budgets.filter(store__isnull=True).first()
    if network is None:
        raise PurchaseBudgetRequired
    override = budgets.filter(store_id=store.pk).first()
    weekly = override.weekly_limit if override and override.weekly_limit is not None else network.weekly_limit
    monthly = override.monthly_limit if override and override.monthly_limit is not None else network.monthly_limit
    if weekly is None or monthly is None or network.week_starts_on is None:
        raise PurchaseBudgetRequired
    return {
        "weeklyLimit": weekly,
        "monthlyLimit": monthly,
        "weekStartsOn": network.week_starts_on,
        "inheritsNetwork": override is None,
        "override": override,
        "network": network,
    }


def _budget_periods(*, organization_id, store, operational_date, budget):
    delta = (operational_date.weekday() - budget["weekStartsOn"]) % 7
    return operational_date - timedelta(days=delta), date(operational_date.year, operational_date.month, 1)


def _committed_spend(*, organization_id, store, week_start, month_start, exclude_order_id=None):
    rows = PurchaseOrder.objects.filter(
        organization_id=organization_id,
        store_id=store.pk,
        status__in=ACTIVE_COMMITMENT_STATUSES,
    )
    if exclude_order_id:
        rows = rows.exclude(pk=exclude_order_id)
    weekly = rows.filter(budget_week_start=week_start).aggregate(total=Sum("committed_amount"))["total"] or Decimal("0")
    monthly = rows.filter(budget_month_start=month_start).aggregate(total=Sum("committed_amount"))["total"] or Decimal("0")
    return weekly, monthly


def _budget_overrun(*, organization_id, store, order, amount, budget, week_start, month_start):
    weekly_used, monthly_used = _committed_spend(
        organization_id=organization_id,
        store=store,
        week_start=week_start,
        month_start=month_start,
        exclude_order_id=order.pk,
    )
    weekly_after = _money(weekly_used + amount)
    monthly_after = _money(monthly_used + amount)
    return (
        weekly_after > budget["weeklyLimit"] or monthly_after > budget["monthlyLimit"],
        weekly_after,
        monthly_after,
    )


def _budget_error(*, budget, weekly_after, monthly_after):
    raise PurchaseBudgetExceeded(
        weekly_limit=budget["weeklyLimit"],
        weekly_after=weekly_after,
        monthly_limit=budget["monthlyLimit"],
        monthly_after=monthly_after,
    )


@transaction.atomic
def review_purchase_order(*, request, store_id, order_id):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    order = get_purchase_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise PurchaseConflict
    store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
    if not _snapshot_is_current(organization_id=organization_id, order=order):
        _mark_stale(organization_id=organization_id, order=order, actor=request.user)
        return None, True
    order.status = PurchaseOrder.Status.REVIEWED
    order.reviewed_by = request.user
    order.reviewed_at = timezone.now()
    order.updated_by = request.user
    order.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_by", "updated_at"])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="reviewed",
        payload={"sourceSnapshot": order.source_snapshot},
    )
    return _order_repr(order), False


@transaction.atomic
def approve_purchase_order(*, request, store_id, order_id, budget_exception_reason=""):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    order = get_purchase_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status != PurchaseOrder.Status.REVIEWED:
        raise PurchaseConflict
    store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
    if not _snapshot_is_current(organization_id=organization_id, order=order):
        _mark_stale(organization_id=organization_id, order=order, actor=request.user)
        return None, True
    operational_date, _ = current_operational_date(organization_id=organization_id, store=store)
    budget = _effective_budget(organization_id=organization_id, store=store, lock=True)
    week_start, month_start = _budget_periods(
        organization_id=organization_id,
        store=store,
        operational_date=operational_date,
        budget=budget,
    )
    amount = _order_totals(order)
    exceeded, weekly_after, monthly_after = _budget_overrun(
        organization_id=organization_id,
        store=store,
        order=order,
        amount=amount,
        budget=budget,
        week_start=week_start,
        month_start=month_start,
    )
    reason = budget_exception_reason.strip()
    if exceeded and not reason:
        _budget_error(budget=budget, weekly_after=weekly_after, monthly_after=monthly_after)
    for item in order.items.select_for_update().all():
        item.approved_quantity = item.quantity
        item.save(update_fields=["approved_quantity"])
    order.status = PurchaseOrder.Status.APPROVED
    order.approved_by = request.user
    order.approved_at = timezone.now()
    order.approved_snapshot = {
        "asOfDate": (order.source_snapshot or {}).get("asOfDate"),
        "supplierId": str(order.supplier_id),
        "items": [
            {
                "productId": str(item.product_id),
                "quantity": _decimal_text(item.quantity),
                "unitPrice": _money_text(item.unit_price),
                "supplierConditionId": str(item.supplier_condition_id),
            }
            for item in order.items.all()
        ],
    }
    order.estimated_total = amount
    order.committed_amount = amount
    order.budget_week_start = week_start
    order.budget_month_start = month_start
    order.budget_exception_reason = reason if exceeded else ""
    order.updated_by = request.user
    order.save(update_fields=[
        "status", "approved_by", "approved_at", "approved_snapshot", "estimated_total",
        "committed_amount", "budget_week_start", "budget_month_start", "budget_exception_reason",
        "updated_by", "updated_at",
    ])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="approved",
        payload={
            "estimatedTotal": _money_text(amount),
            "budgetWeekStart": week_start.isoformat(),
            "budgetMonthStart": month_start.isoformat(),
            "weeklyAfter": _money_text(weekly_after),
            "monthlyAfter": _money_text(monthly_after),
            "budgetException": exceeded,
        },
        reason=reason if exceeded else "",
    )
    return _order_repr(order), False


def _actual_received_total(*, order, new_receipts=None):
    total = Decimal("0")
    for item in order.items.all():
        lines = list(item.receipt_lines.all())
        if new_receipts:
            lines.extend(new_receipts.get(item.pk, []))
        total += sum((line.accepted_quantity * line.actual_unit_price for line in lines), Decimal("0"))
    return _money(total)


@transaction.atomic
def receive_purchase_order(*, request, store_id, order_id, supplier_reference, items, budget_exception_reason=""):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    order = get_purchase_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status not in (PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTIALLY_RECEIVED):
        raise PurchaseConflict
    store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
    operational_date, _ = current_operational_date(organization_id=organization_id, store=store)
    order_items = {
        item.product_id: item
        for item in order.items.select_for_update().select_related("product").all()
    }
    planned = []
    planned_lines = {}
    for row in items:
        item = order_items.get(row["productId"])
        if item is None or item.approved_quantity is None:
            raise PurchaseUnavailable
        physical = row["physicalQuantity"]
        if physical <= 0:
            raise PurchaseValidationError("A quantidade física deve ser maior que zero.")
        remaining = max(Decimal("0"), item.approved_quantity - item.received_quantity)
        accepted = min(physical, remaining)
        excess = physical - accepted
        planned.append((item, physical, accepted, excess, row["actualUnitPrice"]))
        planned_lines[item.pk] = [
            PurchaseReceiptLine(
                accepted_quantity=accepted,
                actual_unit_price=row["actualUnitPrice"],
            )
        ]
    new_received_total = _actual_received_total(order=order, new_receipts=planned_lines)
    remaining_estimate = Decimal("0")
    accepted_by_item = {item.pk: accepted for item, _, accepted, _, _ in planned}
    for item in order.items.all():
        approved = item.approved_quantity or Decimal("0")
        received_after = item.received_quantity + accepted_by_item.get(item.pk, Decimal("0"))
        remaining_estimate += max(Decimal("0"), approved - received_after) * item.unit_price
    new_commitment = _money(new_received_total + remaining_estimate)
    budget = _effective_budget(organization_id=organization_id, store=store, lock=True)
    exceeded, weekly_after, monthly_after = _budget_overrun(
        organization_id=organization_id,
        store=store,
        order=order,
        amount=new_commitment,
        budget=budget,
        week_start=order.budget_week_start,
        month_start=order.budget_month_start,
    )
    reason = budget_exception_reason.strip()
    if exceeded and not reason:
        _budget_error(budget=budget, weekly_after=weekly_after, monthly_after=monthly_after)

    receipt = PurchaseReceipt.objects.create(
        organization_id=organization_id,
        order=order,
        operational_date=operational_date,
        supplier_reference=supplier_reference.strip(),
        budget_exception_reason=reason if exceeded else "",
        received_by=request.user,
    )
    for item, physical, accepted, excess, actual_price in planned:
        line = PurchaseReceiptLine.objects.create(
            organization_id=organization_id,
            receipt=receipt,
            order_item=item,
            physical_quantity=physical,
            accepted_quantity=accepted,
            pending_excess_quantity=excess,
            actual_unit_price=actual_price,
            excess_decision="pending" if excess > 0 else "accepted",
        )
        if accepted > 0:
            StockMovement.objects.create(
                organization_id=organization_id,
                store=store,
                product=item.product,
                kind=StockMovement.Kind.RECEIPT,
                operational_date=operational_date,
                quantity_delta=accepted * item.base_units_per_purchase_unit,
                receipt_line=line,
                created_by=request.user,
            )
        item.received_quantity += accepted
        item.save(update_fields=["received_quantity"])

    all_received = all(
        item.received_quantity >= (item.approved_quantity or Decimal("0"))
        for item in order.items.all()
    )
    order.status = PurchaseOrder.Status.RECEIVED if all_received else PurchaseOrder.Status.PARTIALLY_RECEIVED
    order.committed_amount = new_commitment
    if exceeded:
        order.budget_exception_reason = reason
    order.updated_by = request.user
    order.save(update_fields=["status", "committed_amount", "budget_exception_reason", "updated_by", "updated_at"])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="receipt_recorded",
        payload={
            "receiptId": str(receipt.pk),
            "committedAmount": _money_text(new_commitment),
            "budgetException": exceeded,
            "pendingExcessLines": sum(1 for _, _, _, excess, _ in planned if excess > 0),
            "lines": [
                {
                    "productId": str(item.product_id),
                    "approvedUnitPrice": _money_text(item.unit_price),
                    "actualUnitPrice": _money_text(actual_price),
                    "physicalQuantity": _decimal_text(physical),
                    "acceptedQuantity": _decimal_text(accepted),
                    "pendingExcessQuantity": _decimal_text(excess),
                }
                for item, physical, accepted, excess, actual_price in planned
            ],
        },
        reason=reason if exceeded else "",
    )
    return _receipt_repr(receipt), _order_repr(order)


@transaction.atomic
def cancel_purchase_order_remaining(*, request, store_id, order_id, reason):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    order = get_purchase_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status not in (PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTIALLY_RECEIVED):
        raise PurchaseConflict
    if not any(
        item.approved_quantity is not None and item.received_quantity < item.approved_quantity
        for item in order.items.select_for_update().all()
    ):
        raise PurchaseConflict
    actual_received = _actual_received_total(order=order)
    released_commitment = max(Decimal("0"), order.committed_amount - actual_received)
    for item in order.items.select_for_update().all():
        item.approved_quantity = item.received_quantity
        item.save(update_fields=["approved_quantity"])
    order.status = PurchaseOrder.Status.CANCELLED
    order.committed_amount = actual_received
    order.cancelled_by = request.user
    order.cancelled_at = timezone.now()
    order.updated_by = request.user
    order.save(update_fields=[
        "status", "committed_amount", "cancelled_by", "cancelled_at", "updated_by", "updated_at",
    ])
    _record_event(
        organization_id=organization_id,
        order=order,
        actor=request.user,
        event_type="remaining_cancelled",
        payload={"releasedCommitment": _money_text(released_commitment)},
        reason=reason.strip(),
    )
    return _order_repr(order)


def get_budget_settings(*, organization_id, store_id=None):
    network = PurchaseBudget.objects.filter(organization_id=organization_id, store__isnull=True).first()
    if store_id is None:
        return {
            "scope": "network",
            "weeklyLimit": _money_text(network.weekly_limit) if network else None,
            "monthlyLimit": _money_text(network.monthly_limit) if network else None,
            "weekStartsOn": network.week_starts_on if network else None,
        }
    store = _get_store(organization_id=organization_id, store_id=store_id)
    override = PurchaseBudget.objects.filter(organization_id=organization_id, store_id=store.pk).first()
    return {
        "scope": "store",
        "storeId": str(store.pk),
        "weeklyLimit": _money_text(override.weekly_limit) if override and override.weekly_limit is not None else None,
        "monthlyLimit": _money_text(override.monthly_limit) if override and override.monthly_limit is not None else None,
        "effectiveWeeklyLimit": _money_text(
            override.weekly_limit if override and override.weekly_limit is not None else network.weekly_limit
        ) if network else None,
        "effectiveMonthlyLimit": _money_text(
            override.monthly_limit if override and override.monthly_limit is not None else network.monthly_limit
        ) if network else None,
        "weekStartsOn": network.week_starts_on if network else None,
        "inheritsNetwork": override is None or (override.weekly_limit is None and override.monthly_limit is None),
    }


@transaction.atomic
def patch_budget_settings(*, request, store_id=None, values):
    organization_id = set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )
    if store_id is None:
        budget = PurchaseBudget.objects.select_for_update().filter(
            organization_id=organization_id,
            store__isnull=True,
        ).first()
        if budget is None:
            if not {"weeklyLimit", "monthlyLimit", "weekStartsOn"}.issubset(values):
                raise PurchaseValidationError("Configure os limites semanal e mensal e o início da semana.")
            if values["weeklyLimit"] is None or values["monthlyLimit"] is None:
                raise PurchaseValidationError("Os limites da rede não podem ficar vazios.")
            budget = PurchaseBudget.objects.create(
                organization_id=organization_id,
                store=None,
                weekly_limit=values["weeklyLimit"],
                monthly_limit=values["monthlyLimit"],
                week_starts_on=values["weekStartsOn"],
                created_by=request.user,
                updated_by=request.user,
            )
        else:
            if any(values.get(key) is None for key in ("weeklyLimit", "monthlyLimit") if key in values):
                raise PurchaseValidationError("Os limites da rede não podem ficar vazios.")
            field_map = {
                "weeklyLimit": "weekly_limit",
                "monthlyLimit": "monthly_limit",
                "weekStartsOn": "week_starts_on",
            }
            for key, value in values.items():
                setattr(budget, field_map[key], value)
            budget.updated_by = request.user
            budget.save(update_fields=[*{field_map[key] for key in values}, "updated_by", "updated_at"])
    else:
        store = _get_store(organization_id=organization_id, store_id=store_id, lock=True)
        if not PurchaseBudget.objects.filter(organization_id=organization_id, store__isnull=True).exists():
            raise PurchaseBudgetRequired
        if "weekStartsOn" in values:
            raise PurchaseValidationError("A semana começa no mesmo dia para toda a rede.")
        budget = PurchaseBudget.objects.select_for_update().filter(
            organization_id=organization_id,
            store=store,
        ).first()
        if budget is None:
            budget = PurchaseBudget.objects.create(
                organization_id=organization_id,
                store=store,
                weekly_limit=values.get("weeklyLimit"),
                monthly_limit=values.get("monthlyLimit"),
                week_starts_on=None,
                created_by=request.user,
                updated_by=request.user,
            )
        else:
            field_map = {"weeklyLimit": "weekly_limit", "monthlyLimit": "monthly_limit"}
            for key, value in values.items():
                setattr(budget, field_map[key], value)
            budget.updated_by = request.user
            budget.save(update_fields=[*{field_map[key] for key in values}, "updated_by", "updated_at"])
    return get_budget_settings(organization_id=organization_id, store_id=store_id)
