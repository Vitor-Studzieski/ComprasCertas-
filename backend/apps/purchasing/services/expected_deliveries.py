"""Confirmed supplier deliveries, ETA audit, and rupture-risk alerts."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.inventory.models import Store
from apps.inventory.services.calendar import current_operational_date
from apps.inventory.services.replenishment import generate_store_recommendations
from apps.purchasing.models import (
    DeliveryRiskAlert,
    ExpectedDelivery,
    ExpectedDeliveryEvent,
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderItem,
)


class DeliveryUnavailable(Exception):
    pass


class DeliveryConflict(Exception):
    pass


class DeliveryValidationError(Exception):
    pass



def _decimal_text(value):
    if value is None:
        return None
    return format(value.normalize(), "f") if value else "0"


def _event(*, organization_id, delivery, actor, event_type, reason="", old_eta=None, new_eta=None, payload=None):
    return ExpectedDeliveryEvent.objects.create(
        organization_id=organization_id,
        delivery=delivery,
        event_type=event_type,
        old_eta_date=old_eta,
        new_eta_date=new_eta,
        reason=reason,
        payload=payload or {},
        actor=actor,
    )


def _delivery_repr(delivery):
    item = delivery.order_item
    return {
        "id": str(delivery.pk),
        "orderId": str(item.order_id),
        "productId": str(item.product_id),
        "sku": item.product.sku,
        "productName": item.product.name,
        "purchaseUnitName": item.purchase_unit_name,
        "baseUnitsPerPurchaseUnit": _decimal_text(item.base_units_per_purchase_unit),
        "etaDate": delivery.eta_date.isoformat(),
        "expectedQuantity": _decimal_text(delivery.expected_quantity),
        "receivedQuantity": _decimal_text(delivery.received_quantity),
        "remainingQuantity": _decimal_text(max(Decimal("0"), delivery.expected_quantity - delivery.received_quantity)),
        "status": delivery.status,
        "createdAt": delivery.created_at.isoformat(),
        "updatedAt": delivery.updated_at.isoformat(),
        "events": [
            {
                "type": event.event_type,
                "oldEtaDate": event.old_eta_date.isoformat() if event.old_eta_date else None,
                "newEtaDate": event.new_eta_date.isoformat() if event.new_eta_date else None,
                "reason": event.reason,
                "at": event.created_at.isoformat(),
                "actorName": event.actor.get_full_name() or event.actor.email,
            }
            for event in delivery.events.select_related("actor").all()
        ],
    }


def _alert_repr(alert):
    delivery = alert.delivery
    item = delivery.order_item
    return {
        "id": str(alert.pk),
        "deliveryId": str(delivery.pk),
        "productId": str(item.product_id),
        "sku": item.product.sku,
        "productName": item.product.name,
        "oldEtaDate": alert.old_eta_date.isoformat(),
        "newEtaDate": alert.new_eta_date.isoformat(),
        "previousStockoutDate": alert.previous_stockout_date.isoformat() if alert.previous_stockout_date else None,
        "projectedStockoutDate": alert.projected_stockout_date.isoformat() if alert.projected_stockout_date else None,
        "status": alert.status,
        "actionNote": alert.action_note,
        "resolutionReason": alert.resolution_reason,
        "createdAt": alert.created_at.isoformat(),
        "acknowledgedAt": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "resolvedAt": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }


def _context(request):
    return set_active_organization_rls_context(
        user=request.user,
        session=request._request.session,
    )


def _get_store(*, organization_id, store_id):
    store = Store.objects.filter(organization_id=organization_id, pk=store_id, is_active=True).first()
    if store is None:
        raise DeliveryUnavailable
    return store


def _get_order(*, organization_id, store_id, order_id, lock=False):
    query = PurchaseOrder.objects.filter(organization_id=organization_id, store_id=store_id)
    if lock:
        query = query.select_for_update()
    order = query.select_related("store", "supplier").filter(pk=order_id).first()
    if order is None:
        raise DeliveryUnavailable
    return order


def _mark_related_drafts_stale(*, organization_id, store_id, product_id, actor):
    from apps.purchasing.services.orders import _mark_stale

    orders = PurchaseOrder.objects.filter(
        organization_id=organization_id,
        store_id=store_id,
        status__in=[PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVIEWED],
        items__product_id=product_id,
    ).distinct()
    for order in orders.select_for_update():
        _mark_stale(organization_id=organization_id, order=order, actor=actor)


def _projected_stockout_date(*, organization_id, store, product_id, as_of_date, eta_overrides=None):
    payload = generate_store_recommendations(
        organization_id=organization_id,
        store=store,
        as_of_date=as_of_date,
    )
    recommendation = next(
        (row for row in payload["recommendations"] if row["product"]["id"] == str(product_id)),
        None,
    )
    if recommendation is None or recommendation.get("averageDailyDemand") in (None, "0"):
        return None
    demand = Decimal(recommendation["averageDailyDemand"])
    balance = Decimal(recommendation["currentStock"] or "0")
    eta_overrides = eta_overrides or {}
    inbound_by_day = {}
    deliveries = ExpectedDelivery.objects.filter(
        organization_id=organization_id,
        order_item__order__store_id=store.pk,
        order_item__product_id=product_id,
        order_item__order__sent_to_supplier_at__isnull=False,
        status=ExpectedDelivery.Status.CONFIRMED,
        received_quantity__lt=F("expected_quantity"),
    ).select_related("order_item")
    for delivery in deliveries:
        eta = eta_overrides.get(delivery.pk, delivery.eta_date)
        remaining = max(Decimal("0"), delivery.expected_quantity - delivery.received_quantity)
        inbound_by_day[eta] = inbound_by_day.get(eta, Decimal("0")) + remaining * delivery.order_item.base_units_per_purchase_unit
    balance += inbound_by_day.pop(as_of_date, Decimal("0"))
    if balance <= 0:
        return as_of_date
    for offset in range(1, 366):
        day = as_of_date + timedelta(days=offset)
        balance += inbound_by_day.pop(day, Decimal("0"))
        balance -= demand
        if balance <= 0:
            return day
    return None


def _stale_recommendations(*, organization_id, store_id, product_id, actor):
    _mark_related_drafts_stale(
        organization_id=organization_id,
        store_id=store_id,
        product_id=product_id,
        actor=actor,
    )


def list_order_delivery_tracking(*, organization_id, store_id, order_id):
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id)
    deliveries = ExpectedDelivery.objects.filter(
        organization_id=organization_id,
        order_item__order=order,
    ).select_related("order_item__product", "order_item__order").prefetch_related("events__actor")
    alerts = DeliveryRiskAlert.objects.filter(
        organization_id=organization_id,
        delivery__order_item__order=order,
    ).select_related("delivery__order_item__product", "delivery__order_item__order")
    return {
        "sentToSupplierAt": order.sent_to_supplier_at.isoformat() if order.sent_to_supplier_at else None,
        "deliveries": [_delivery_repr(row) for row in deliveries],
        "alerts": [_alert_repr(row) for row in alerts],
    }


@transaction.atomic
def mark_purchase_order_sent(*, request, store_id, order_id):
    organization_id = _context(request)
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status not in (PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTIALLY_RECEIVED):
        raise DeliveryConflict
    if order.sent_to_supplier_at is not None:
        raise DeliveryConflict
    order.sent_to_supplier_at = timezone.now()
    order.sent_to_supplier_by = request.user
    order.updated_by = request.user
    order.save(update_fields=["sent_to_supplier_at", "sent_to_supplier_by", "updated_by", "updated_at"])
    PurchaseOrderEvent.objects.create(
        organization_id=organization_id,
        order=order,
        event_type="sent_to_supplier",
        payload={"sentToSupplierAt": order.sent_to_supplier_at.isoformat()},
        actor=request.user,
    )
    return {
        "orderId": str(order.pk),
        "sentToSupplierAt": order.sent_to_supplier_at.isoformat(),
        "sentBy": request.user.get_full_name() or request.user.email,
    }


@transaction.atomic
def create_expected_delivery(*, request, store_id, order_id, product_id, quantity, eta_date):
    organization_id = _context(request)
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    if order.status not in (PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTIALLY_RECEIVED):
        raise DeliveryConflict
    if order.sent_to_supplier_at is None:
        raise DeliveryConflict
    store = _get_store(organization_id=organization_id, store_id=store_id)
    as_of_date, _ = current_operational_date(organization_id=organization_id, store=store)
    if eta_date < as_of_date:
        raise DeliveryValidationError("A data confirmada de entrega não pode estar no passado.")
    item = PurchaseOrderItem.objects.select_for_update().filter(
        organization_id=organization_id,
        order=order,
        product_id=product_id,
    ).select_related("product").first()
    if item is None or item.approved_quantity is None:
        raise DeliveryUnavailable
    active_expected = ExpectedDelivery.objects.filter(
        organization_id=organization_id,
        order_item=item,
        status=ExpectedDelivery.Status.CONFIRMED,
    ).aggregate(total=Sum(F("expected_quantity") - F("received_quantity")))["total"] or Decimal("0")
    remaining_approved = max(Decimal("0"), item.approved_quantity - item.received_quantity - active_expected)
    if quantity <= 0 or quantity > remaining_approved:
        raise DeliveryValidationError("A quantidade excede o saldo aprovado ainda sem prazo de entrega confirmado.")
    delivery = ExpectedDelivery.objects.create(
        organization_id=organization_id,
        order_item=item,
        eta_date=eta_date,
        expected_quantity=quantity,
        confirmed_by=request.user,
        updated_by=request.user,
    )
    _event(
        organization_id=organization_id,
        delivery=delivery,
        actor=request.user,
        event_type="eta_confirmed",
        new_eta=eta_date,
        payload={"quantity": _decimal_text(quantity)},
    )
    _stale_recommendations(
        organization_id=organization_id,
        store_id=store_id,
        product_id=product_id,
        actor=request.user,
    )
    return _delivery_repr(delivery)


@transaction.atomic
def update_expected_delivery_eta(*, request, store_id, order_id, delivery_id, eta_date, reason):
    organization_id = _context(request)
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    store = _get_store(organization_id=organization_id, store_id=store_id)
    delivery = ExpectedDelivery.objects.select_for_update().filter(
        organization_id=organization_id,
        order_item__order=order,
        pk=delivery_id,
        status=ExpectedDelivery.Status.CONFIRMED,
    ).select_related("order_item__product").first()
    if delivery is None:
        raise DeliveryUnavailable
    as_of_date, _ = current_operational_date(organization_id=organization_id, store=store)
    if eta_date < as_of_date:
        raise DeliveryValidationError("A nova data confirmada não pode estar no passado.")
    old_eta = delivery.eta_date
    if eta_date == old_eta:
        raise DeliveryValidationError("A nova data deve ser diferente da data atual.")
    before = _projected_stockout_date(
        organization_id=organization_id,
        store=store,
        product_id=delivery.order_item.product_id,
        as_of_date=as_of_date,
        eta_overrides={delivery.pk: old_eta},
    )
    delivery.eta_date = eta_date
    delivery.updated_by = request.user
    delivery.save(update_fields=["eta_date", "updated_by", "updated_at"])
    after = _projected_stockout_date(
        organization_id=organization_id,
        store=store,
        product_id=delivery.order_item.product_id,
        as_of_date=as_of_date,
        eta_overrides={delivery.pk: eta_date},
    )
    _event(
        organization_id=organization_id,
        delivery=delivery,
        actor=request.user,
        event_type="eta_changed",
        old_eta=old_eta,
        new_eta=eta_date,
        reason=reason.strip(),
        payload={
            "projectedStockoutBefore": before.isoformat() if before else None,
            "projectedStockoutAfter": after.isoformat() if after else None,
        },
    )
    alert = None
    if after is not None and (before is None or after < before):
        alert = DeliveryRiskAlert.objects.create(
            organization_id=organization_id,
            delivery=delivery,
            old_eta_date=old_eta,
            new_eta_date=eta_date,
            previous_stockout_date=before,
            projected_stockout_date=after,
        )
    _stale_recommendations(
        organization_id=organization_id,
        store_id=store_id,
        product_id=delivery.order_item.product_id,
        actor=request.user,
    )
    return {"delivery": _delivery_repr(delivery), "alert": _alert_repr(alert) if alert else None}


@transaction.atomic
def acknowledge_delivery_risk(*, request, store_id, order_id, alert_id, action_note):
    organization_id = _context(request)
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    alert = DeliveryRiskAlert.objects.select_for_update().filter(
        organization_id=organization_id,
        delivery__order_item__order=order,
        pk=alert_id,
        status=DeliveryRiskAlert.Status.OPEN,
    ).first()
    if alert is None:
        raise DeliveryConflict
    alert.status = DeliveryRiskAlert.Status.ACKNOWLEDGED
    alert.action_note = action_note.strip()
    alert.acknowledged_by = request.user
    alert.acknowledged_at = timezone.now()
    alert.save(update_fields=["status", "action_note", "acknowledged_by", "acknowledged_at"])
    _event(
        organization_id=organization_id,
        delivery=alert.delivery,
        actor=request.user,
        event_type="risk_alert_acknowledged",
        reason=action_note.strip(),
        payload={"alertId": str(alert.pk)},
    )
    return _alert_repr(alert)


@transaction.atomic
def resolve_delivery_risk(*, request, store_id, order_id, alert_id, resolution, reason):
    organization_id = _context(request)
    order = _get_order(organization_id=organization_id, store_id=store_id, order_id=order_id, lock=True)
    alert = DeliveryRiskAlert.objects.select_for_update().filter(
        organization_id=organization_id,
        delivery__order_item__order=order,
        pk=alert_id,
        status=DeliveryRiskAlert.Status.ACKNOWLEDGED,
    ).first()
    if alert is None:
        raise DeliveryConflict
    alert.status = resolution
    alert.resolution_reason = reason.strip()
    alert.resolved_by = request.user
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["status", "resolution_reason", "resolved_by", "resolved_at"])
    _event(
        organization_id=organization_id,
        delivery=alert.delivery,
        actor=request.user,
        event_type=f"risk_alert_{resolution}",
        reason=reason.strip(),
        payload={"alertId": str(alert.pk)},
    )
    return _alert_repr(alert)


@transaction.atomic
def allocate_expected_receipt(*, organization_id, order_item, accepted_quantity, actor):
    remaining = accepted_quantity
    deliveries = ExpectedDelivery.objects.select_for_update().filter(
        organization_id=organization_id,
        order_item=order_item,
        status=ExpectedDelivery.Status.CONFIRMED,
    ).order_by("eta_date", "created_at", "id")
    for delivery in deliveries:
        open_quantity = max(Decimal("0"), delivery.expected_quantity - delivery.received_quantity)
        amount = min(remaining, open_quantity)
        if amount <= 0:
            continue
        delivery.received_quantity += amount
        if delivery.received_quantity >= delivery.expected_quantity:
            delivery.status = ExpectedDelivery.Status.RECEIVED
        delivery.updated_by = actor
        delivery.save(update_fields=["received_quantity", "status", "updated_by", "updated_at"])
        _event(
            organization_id=organization_id,
            delivery=delivery,
            actor=actor,
            event_type="receipt_allocated",
            payload={"acceptedQuantity": _decimal_text(amount)},
        )
        remaining -= amount
        if remaining <= 0:
            break


@transaction.atomic
def cancel_expected_deliveries_for_order(*, organization_id, order, actor, reason):
    deliveries = ExpectedDelivery.objects.select_for_update().filter(
        organization_id=organization_id,
        order_item__order=order,
        status=ExpectedDelivery.Status.CONFIRMED,
    )
    for delivery in deliveries:
        delivery.status = ExpectedDelivery.Status.CANCELLED
        delivery.updated_by = actor
        delivery.save(update_fields=["status", "updated_by", "updated_at"])
        _event(
            organization_id=organization_id,
            delivery=delivery,
            actor=actor,
            event_type="remaining_delivery_cancelled",
            reason=reason.strip(),
            payload={"remainingQuantity": _decimal_text(delivery.expected_quantity - delivery.received_quantity)},
        )
