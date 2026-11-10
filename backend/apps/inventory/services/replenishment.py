"""Deterministic, explainable replenishment calculations."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from django.db.models import Q, Sum

from apps.catalog.models import SupplierProductCondition
from apps.inventory.models import (
    DailyProductSale,
    DailySalesCoverage,
    DailyStockAvailability,
    Product,
    ReplenishmentPolicy,
    StockMovement,
    Store,
    StoreProduct,
)


def round_up_to_multiple(quantity: Decimal, multiple: Decimal) -> Decimal:
    """Return the smallest non-negative multiple that is at least quantity."""
    if quantity <= 0:
        return Decimal("0")
    if multiple <= 0:
        raise ValueError("O múltiplo de compra deve ser maior que zero.")
    count = (quantity / multiple).to_integral_value(rounding=ROUND_CEILING)
    return count * multiple


def purchase_quantity_for_need(
    need_in_base_units: Decimal,
    *,
    base_units_per_purchase_unit: Decimal,
    minimum_purchase_quantity: Decimal,
    purchase_multiple: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return (purchase units, normalized base units) respecting MOQ and packs."""
    if need_in_base_units <= 0:
        return Decimal("0"), Decimal("0")
    if base_units_per_purchase_unit <= 0 or purchase_multiple <= 0:
        raise ValueError("A conversão e o múltiplo de compra devem ser positivos.")

    need_in_purchase_units = need_in_base_units / base_units_per_purchase_unit
    quantity = max(need_in_purchase_units, minimum_purchase_quantity)
    quantity = round_up_to_multiple(quantity, purchase_multiple)
    return quantity, quantity * base_units_per_purchase_unit


def average_daily_demand(
    sales_by_date: dict,
    *,
    valid_dates: set,
    out_of_stock_dates: set,
) -> tuple[Decimal | None, int, int]:
    """Average sales on dates with explicit sales or declared complete coverage."""
    included_dates = valid_dates - out_of_stock_dates
    if not included_dates:
        return None, 0, len(out_of_stock_dates & valid_dates)
    total = sum((sales_by_date.get(day, Decimal("0")) for day in included_dates), Decimal("0"))
    return (
        (total / Decimal(len(included_dates))).quantize(Decimal("0.000001")),
        len(included_dates),
        len(out_of_stock_dates & valid_dates),
    )


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _ceil_stock_quantity(quantity: Decimal, unit: str) -> Decimal:
    quantum = Decimal("1") if unit == Product.Unit.UNIT else Decimal("0.001")
    return quantity.quantize(quantum, rounding=ROUND_CEILING)


def _resolve_parameter(policies, product, store, field):
    sources = [
        ("store_product", (None, product.pk, store.pk)),
        ("product", (None, product.pk, None)),
    ]
    if product.category_id is not None:
        sources.append(("category", (product.category_id, None, None)))
    sources.append(("network", (None, None, None)))
    for source, key in sources:
        policy = policies.get(key)
        value = getattr(policy, field, None) if policy else None
        if value is not None:
            return value, source
    return None, None


def _effective_supplier_conditions(*, organization_id, store, product, as_of_date):
    conditions = list(
        SupplierProductCondition.objects.filter(
            organization_id=organization_id,
            product_id=product.pk,
            effective_from__lte=as_of_date,
        )
        .filter(Q(store_id=store.pk) | Q(store__isnull=True))
        .select_related("supplier")
        .order_by("supplier_id", "store_id", "-effective_from", "-id")
    )
    by_supplier = {}
    for condition in conditions:
        current = by_supplier.get(condition.supplier_id)
        if current is None or (current.store_id is None and condition.store_id == store.pk):
            by_supplier[condition.supplier_id] = condition
    return [condition for condition in by_supplier.values() if condition.is_available and condition.supplier.is_active]


def generate_store_recommendations(*, organization_id, store: Store, as_of_date: date):
    """Calculate explainable replenishment suggestions from tenant-scoped records."""
    window_end = as_of_date - timedelta(days=1)
    window_start = as_of_date - timedelta(days=30)
    assortments = list(
        StoreProduct.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            effective_from__lte=as_of_date,
            product__is_active=True,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=as_of_date))
        .select_related("product", "product__category")
        .order_by("product__sku", "product_id")
    )
    products = [assortment.product for assortment in assortments]
    product_ids = [product.pk for product in products]
    if not products:
        return {
            "storeId": str(store.pk),
            "asOfDate": as_of_date.isoformat(),
            "ruleVersion": "deterministic-v1",
            "generatedAt": None,
            "reviewIntervalDays": None,
            "window": {"days": 30, "startDate": window_start.isoformat(), "endDate": window_end.isoformat()},
            "limitations": [
                "expected_deliveries_not_modeled",
                "budget_not_modeled",
                "freight_not_recorded",
            ],
            "recommendations": [],
        }

    policies = {
        (policy.category_id, policy.product_id, policy.store_id): policy
        for policy in ReplenishmentPolicy.objects.filter(organization_id=organization_id)
    }
    sales = list(
        DailyProductSale.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id__in=product_ids,
            operational_date__gte=window_start,
            operational_date__lte=window_end,
        ).values("product_id", "operational_date", "quantity")
    )
    sales_by_product = {product_id: {} for product_id in product_ids}
    for sale in sales:
        sales_by_product[sale["product_id"]][sale["operational_date"]] = sale["quantity"]

    coverage_periods = list(
        DailySalesCoverage.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            operational_date_from__lte=window_end,
            operational_date_to__gte=window_start,
        )
    )
    out_of_stock_rows = list(
        DailyStockAvailability.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id__in=product_ids,
            operational_date__gte=window_start,
            operational_date__lte=window_end,
        ).values("product_id", "operational_date")
    )
    out_of_stock_by_product = {product_id: set() for product_id in product_ids}
    for row in out_of_stock_rows:
        out_of_stock_by_product[row["product_id"]].add(row["operational_date"])

    stock_totals = {
        row["product_id"]: row["total"] or Decimal("0")
        for row in StockMovement.objects.filter(
            organization_id=organization_id,
            store_id=store.pk,
            product_id__in=product_ids,
        ).values("product_id").annotate(total=Sum("quantity_delta"))
    }

    recommendations = []
    for assortment in assortments:
        product = assortment.product
        active_dates = {
            window_start + timedelta(days=offset)
            for offset in range(30)
            if assortment.effective_from <= window_start + timedelta(days=offset)
            and (assortment.effective_to is None or window_start + timedelta(days=offset) < assortment.effective_to)
        }
        complete_dates = set()
        for period in coverage_periods:
            start = max(window_start, period.operational_date_from, assortment.effective_from)
            end = min(window_end, period.operational_date_to)
            if assortment.effective_to is not None:
                end = min(end, assortment.effective_to - timedelta(days=1))
            if start <= end:
                complete_dates.update(start + timedelta(days=offset) for offset in range((end - start).days + 1))
        explicit_dates = set(sales_by_product[product.pk]) & active_dates
        out_of_stock_dates = out_of_stock_by_product[product.pk] & active_dates
        valid_dates = (complete_dates | explicit_dates | out_of_stock_dates) & active_dates
        demand, valid_days, excluded_days = average_daily_demand(
            sales_by_product[product.pk],
            valid_dates=valid_dates,
            out_of_stock_dates=out_of_stock_dates,
        )
        complete_valid_days = len((complete_dates & valid_dates) - out_of_stock_dates)
        explicit_valid_days = len(((explicit_dates - complete_dates) & valid_dates) - out_of_stock_dates)

        minimum_stock, minimum_source = _resolve_parameter(
            policies, product, store, "minimum_stock"
        )
        safety_days, safety_source = _resolve_parameter(policies, product, store, "safety_days")
        network_policy = policies.get((None, None, None))
        store_policy = policies.get((None, None, store.pk))
        review_interval_days = (
            store_policy.review_interval_days if store_policy and store_policy.review_interval_days is not None
            else network_policy.review_interval_days if network_policy else None
        )
        review_interval_source = (
            "store" if store_policy and store_policy.review_interval_days is not None
            else "network" if network_policy and network_policy.review_interval_days is not None
            else None
        )
        configured_sources = list(dict.fromkeys(
            source for source in (minimum_source, safety_source, review_interval_source) if source is not None
        ))
        parameter_source = (
            configured_sources[0]
            if len(configured_sources) == 1
            else "mixed" if configured_sources else "unconfigured"
        )

        current_stock = stock_totals.get(product.pk, Decimal("0"))
        coverage_days = (
            (current_stock / demand if current_stock > 0 else Decimal("0"))
            if demand is not None and demand > 0
            else None
        )
        stock_deficit = max(Decimal("0"), -current_stock)
        if demand is None:
            target_stock = (
                _ceil_stock_quantity(minimum_stock, product.unit)
                if minimum_stock is not None
                else None
            )
        elif review_interval_days is not None and safety_days is not None:
            target_stock = _ceil_stock_quantity(
                demand * Decimal(review_interval_days + safety_days), product.unit
            )
        else:
            target_stock = None

        conditions = _effective_supplier_conditions(
            organization_id=organization_id,
            store=store,
            product=product,
            as_of_date=as_of_date,
        )
        supplier_rows = []
        for condition in conditions:
            lead_days = condition.delivery_lead_time_days
            consumption_to_arrival = (demand or Decimal("0")) * Decimal(lead_days)
            stock_at_arrival = current_stock - consumption_to_arrival
            predicted_shortage = max(Decimal("0"), consumption_to_arrival - current_stock)
            quantity_needed = (
                max(Decimal("0"), target_stock - stock_at_arrival)
                if target_stock is not None
                else None
            )
            if quantity_needed is not None:
                quantity_for_pack = _ceil_stock_quantity(quantity_needed, product.unit)
                order_quantity, order_quantity_base = purchase_quantity_for_need(
                    quantity_for_pack,
                    base_units_per_purchase_unit=condition.base_units_per_purchase_unit,
                    minimum_purchase_quantity=condition.minimum_purchase_quantity,
                    purchase_multiple=condition.purchase_multiple,
                )
                excess_over_target = max(Decimal("0"), order_quantity_base - quantity_needed)
            else:
                order_quantity = order_quantity_base = excess_over_target = None
            supplier_rows.append({
                "condition": condition,
                "stock_at_arrival": stock_at_arrival,
                "predicted_shortage": predicted_shortage,
                "quantity_needed": quantity_needed,
                "order_quantity": order_quantity,
                "order_quantity_base": order_quantity_base,
                "excess_over_target": excess_over_target,
                "base_unit_cost": condition.price_per_purchase_unit / condition.base_units_per_purchase_unit,
            })

        safe_rows = [row for row in supplier_rows if row["predicted_shortage"] == 0]
        at_risk_rows = [row for row in supplier_rows if row["predicted_shortage"] > 0]
        safe_rows.sort(key=lambda row: (
            row["base_unit_cost"],
            row["condition"].delivery_lead_time_days,
            row["condition"].supplier.name.casefold(),
        ))
        at_risk_rows.sort(key=lambda row: (
            row["condition"].delivery_lead_time_days,
            row["predicted_shortage"],
            row["base_unit_cost"],
            row["condition"].supplier.name.casefold(),
        ))
        supplier_rows = safe_rows + at_risk_rows if safe_rows else at_risk_rows
        # Supplier order is decided by the ranked comparison. Do not skip a
        # preferred safe supplier with zero need and accidentally select a
        # slower supplier that would create a positive order requirement.
        selected = (
            supplier_rows[0]
            if supplier_rows
            and target_stock is not None
            and supplier_rows[0]["order_quantity_base"] is not None
            and supplier_rows[0]["order_quantity_base"] > 0
            else None
        )
        selected_id = selected["condition"].supplier_id if selected else None
        warnings = []
        if valid_days < 30:
            warnings.append("low_evidence")
        if demand is None and minimum_stock is None:
            warnings.append("missing_minimum_stock")
        if review_interval_days is None and demand is not None:
            warnings.append("review_interval_missing")
        if safety_days is None and demand is not None:
            warnings.append("safety_days_missing")
        if not supplier_rows:
            warnings.append("supplier_condition_missing")
        if any(row["predicted_shortage"] > 0 for row in supplier_rows):
            warnings.append("stockout_before_arrival")
        if supplier_rows:
            warnings.append("freight_unknown_cost_partial")

        supplier_comparisons = []
        for index, row in enumerate(supplier_rows, start=1):
            condition = row["condition"]
            reason_codes = []
            if row["predicted_shortage"] == 0:
                reason_codes.append("arrives_before_predicted_stockout")
            else:
                reason_codes.append("stockout_predicted_before_arrival")
            if row is selected:
                reason_codes.append(
                    "selected_shortest_lead_time_when_all_at_risk"
                    if not safe_rows
                    else "selected_lowest_known_unit_cost_after_risk"
                )
            reason_codes.append("freight_unknown")
            supplier_comparisons.append({
                "supplierId": str(condition.supplier_id),
                "supplierName": condition.supplier.name,
                "availability": "available",
                "deliveryLeadTimeDays": condition.delivery_lead_time_days,
                "unitCost": _decimal_text(condition.price_per_purchase_unit),
                "purchaseUnitName": condition.purchase_unit_name,
                "baseUnitsPerPurchaseUnit": _decimal_text(condition.base_units_per_purchase_unit),
                "minimumPurchaseQuantity": _decimal_text(condition.minimum_purchase_quantity),
                "purchaseMultiple": _decimal_text(condition.purchase_multiple),
                "freightStatus": "unknown",
                "costCompleteness": "partial",
                "partialCost": True,
                "stockAtArrival": _decimal_text(row["stock_at_arrival"]),
                "predictedShortageBeforeArrival": _decimal_text(row["predicted_shortage"]),
                "quantityNeededBase": _decimal_text(row["quantity_needed"]),
                "orderQuantity": _decimal_text(row["order_quantity"]),
                "orderQuantityBase": _decimal_text(row["order_quantity_base"]),
                "excessOverTarget": _decimal_text(row["excess_over_target"]),
                "selected": row is selected,
                "ruptureAvoidanceRank": index,
                "costRank": None,
                "reasonCodes": reason_codes,
            })

        recommendations.append({
            "product": {
                "id": str(product.pk),
                "sku": product.sku,
                "name": product.name,
                "baseUnit": product.unit,
                "categoryId": str(product.category_id) if product.category_id else None,
                "categoryName": product.category.name if product.category_id else None,
            },
            "currentStock": _decimal_text(current_stock),
            "averageDailyDemand": _decimal_text(demand),
            "validDemandDays": valid_days,
            "lowEvidence": valid_days < 30,
            "evidence": {
                "demandWindowStart": window_start.isoformat(),
                "demandWindowEnd": window_end.isoformat(),
                "excludedOutOfStockDays": excluded_days,
                "completeSalesDays": complete_valid_days,
                "explicitSalesDays": explicit_valid_days,
            },
            "coverageDays": _decimal_text(coverage_days),
            "stockDeficit": _decimal_text(stock_deficit),
            "effectiveParameters": {
                "minimumStock": _decimal_text(minimum_stock),
                "minimumStockSource": minimum_source,
                "safetyDays": safety_days,
                "safetyDaysSource": safety_source,
                "reviewIntervalDays": review_interval_days,
                "reviewIntervalSource": review_interval_source,
                "source": parameter_source,
            },
            "targetStock": _decimal_text(target_stock),
            "suggestedQuantity": _decimal_text(selected["quantity_needed"] if selected else None),
            "quantityToOrder": _decimal_text(selected["order_quantity_base"] if selected else None),
            "excessOverTarget": _decimal_text(selected["excess_over_target"] if selected else None),
            "selectedSupplierId": str(selected_id) if selected_id else None,
            "reasonCodes": (
                (["low_evidence"] if valid_days < 30 else [])
                + (["missing_minimum_stock"] if demand is None and minimum_stock is None else [])
                + (["stockout_before_arrival"] if selected and selected["predicted_shortage"] > 0 else [])
                + (["partial_cost"] if selected else [])
            ),
            "warnings": warnings,
            "supplierComparisons": supplier_comparisons,
        })

    from django.utils import timezone

    network_policy = policies.get((None, None, None))
    store_policy = policies.get((None, None, store.pk))
    effective_review_interval = (
        store_policy.review_interval_days if store_policy and store_policy.review_interval_days is not None
        else network_policy.review_interval_days if network_policy else None
    )

    return {
        "storeId": str(store.pk),
        "asOfDate": as_of_date.isoformat(),
        "ruleVersion": "deterministic-v1",
        "generatedAt": timezone.now().isoformat(),
        "reviewIntervalDays": effective_review_interval,
        "window": {"days": 30, "startDate": window_start.isoformat(), "endDate": window_end.isoformat()},
        "limitations": ["expected_deliveries_not_modeled", "budget_not_modeled", "freight_not_recorded"],
        "recommendations": recommendations,
    }
