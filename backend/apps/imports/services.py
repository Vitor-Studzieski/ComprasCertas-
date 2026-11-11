"""Validation previews and all-or-nothing application of spreadsheet imports."""

import hashlib
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.catalog.models import Supplier, SupplierProductCondition
from apps.identity.services.organization_context import set_active_organization_rls_context
from apps.imports.models import ImportBatch
from apps.imports.parsers import parse_decimal, parse_operational_date
from apps.inventory.models import (
    DailyProductSale,
    DailySalesCoverage,
    DailyStockAvailability,
    OpeningStockBatch,
    Product,
    ProductCategory,
    Store,
    StoreProduct,
)
from apps.inventory.services.calendar import (
    OperationalCalendarMissing,
    current_operational_date,
    resolve_operational_calendar,
)
from apps.inventory.services.daily_sales import (
    DailySaleAlreadyRecorded,
    DailySaleDateInFuture,
    IdempotencyConflict,
    InvalidDailySale,
    OpeningStockRequired as SalesOpeningStockRequired,
    ProductNotAssorted as SalesProductNotAssorted,
    ProductUnavailable as SalesProductUnavailable,
    StoreUnavailable as SalesStoreUnavailable,
    create_daily_sale,
)
from apps.inventory.services.opening_stock import (
    InvalidOpeningStock,
    OpeningDateInFuture,
    OpeningStockAlreadyRecorded,
    ProductNotAssorted as OpeningProductNotAssorted,
    ProductUnavailable as OpeningProductUnavailable,
    StoreUnavailable as OpeningStoreUnavailable,
    confirm_opening_stock,
)


PREVIEW_TTL = timedelta(minutes=30)
MAX_ISSUES = 500
MAX_CHANGES = 200


class ImportUnavailable(Exception):
    """The batch or one of its referenced records is unavailable."""


class ImportNoLongerConfirmable(Exception):
    def __init__(self, *, code, message, preview=None, status=409):
        super().__init__(message)
        self.code = code
        self.preview = preview
        self.status = status


def _decimal_or_issue(value, *, row, field, issues, places=3, allow_zero=True):
    try:
        return Decimal(parse_decimal(value, decimal_places=places, allow_zero=allow_zero))
    except (ValueError, ArithmeticError):
        issues.append({
            "rowNumber": row.get("rowNumber"),
            "code": "quantity_invalid",
            "field": field,
            "message": f"{field}: informe um número não negativo, com no máximo {places} casas decimais.",
        })
        return None


def _date_or_issue(value, *, row, field, issues):
    try:
        return parse_operational_date(value)
    except (ValueError, TypeError):
        issues.append({
            "rowNumber": row.get("rowNumber"),
            "code": "date_invalid",
            "field": field,
            "message": f"{field}: informe a data como DD/MM/AAAA.",
        })
        return None


def _quantity_valid_for_unit(quantity, unit):
    if quantity is None or quantity < 0:
        return False
    if unit == Product.Unit.UNIT:
        return quantity == quantity.to_integral_value()
    return quantity.as_tuple().exponent >= -3


def _snapshot_fingerprint(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_change(*, row, key, action, before=None, after=None):
    return {
        "rowNumber": row.get("rowNumber"),
        "key": str(key),
        "action": action,
        "before": before or {},
        "after": after or {},
    }


def _product_snapshot(product):
    if product is None:
        return None
    return {
        "id": str(product.pk),
        "sku": product.sku,
        "name": product.name,
        "unit": product.unit,
        "categoryId": str(product.category_id) if product.category_id else None,
        "categoryName": product.category.name if product.category_id else None,
        "active": product.is_active,
    }


def _supplier_snapshot(supplier):
    if supplier is None:
        return None
    return {
        "id": str(supplier.pk),
        "name": supplier.name,
        "contactName": supplier.contact_name,
        "email": supplier.email,
        "phone": supplier.phone,
        "notes": supplier.notes,
        "isActive": supplier.is_active,
    }


def _condition_snapshot(condition):
    return {
        "id": str(condition.pk),
        "effectiveFrom": condition.effective_from.isoformat(),
        "purchaseUnitName": condition.purchase_unit_name,
        "baseUnitsPerPurchaseUnit": str(condition.base_units_per_purchase_unit),
        "pricePerPurchaseUnit": str(condition.price_per_purchase_unit),
        "deliveryLeadTimeDays": condition.delivery_lead_time_days,
        "minimumPurchaseQuantity": str(condition.minimum_purchase_quantity),
        "purchaseMultiple": str(condition.purchase_multiple),
        "isAvailable": condition.is_available,
    }


def _inspect_products(*, rows, organization_id):
    issues, changes, snapshot, plan = [], [], [], []
    skus = {row.get("sku", "") for row in rows}
    products = {
        product.sku: product
        for product in Product.objects.select_for_update().filter(
            organization_id=organization_id, sku__in=skus
        ).select_related("category")
    }
    category_names = {row.get("category", "").casefold() for row in rows if row.get("category")}
    categories = {
        category.name.casefold(): category
        for category in ProductCategory.objects.select_for_update().filter(organization_id=organization_id)
        if category.name.casefold() in category_names
    }
    seen = set()
    for row in rows:
        sku, name, unit, category_name = (row.get(k, "") for k in ("sku", "name", "unit", "category"))
        row_number = row.get("rowNumber")
        if not sku or len(sku) > 64:
            issues.append({"rowNumber": row_number, "code": "sku_invalid", "field": "SKU", "message": "Informe um SKU de até 64 caracteres."})
        if sku in seen:
            issues.append({"rowNumber": row_number, "code": "duplicate_sku_in_file", "field": "SKU", "message": f"O SKU {sku} aparece mais de uma vez no arquivo."})
        seen.add(sku)
        if not name or len(name) > 200:
            issues.append({"rowNumber": row_number, "code": "product_name_invalid", "field": "Nome", "message": "Informe um nome de produto de até 200 caracteres."})
        if len(category_name) > 120:
            issues.append({"rowNumber": row_number, "code": "product_category_invalid", "field": "Categoria", "message": "O nome da categoria deve ter até 120 caracteres."})
        if unit not in Product.Unit.values:
            issues.append({"rowNumber": row_number, "code": "product_unit_invalid", "field": "Unidade", "message": "Use unidade, kg ou litro no campo Unidade."})
        product = products.get(sku)
        category = categories.get(category_name.casefold()) if category_name else None
        if category_name and category is None:
            snapshot.append({"type": "category", "name": category_name.casefold(), "id": None})
        if product and not product.is_active:
            issues.append({"rowNumber": row_number, "code": "product_inactive", "field": "SKU", "message": f"O produto {sku} está inativo e não pode ser atualizado pela planilha."})
        if product and unit in Product.Unit.values and product.unit != unit:
            issues.append({"rowNumber": row_number, "code": "product_unit_change_requires_review", "field": "Unidade", "message": f"A unidade-base de {sku} não pode ser alterada por importação, pois já pode estar vinculada a movimentos de estoque."})

        before = _product_snapshot(product)
        after = {"sku": sku, "name": name, "unit": unit, "category": category_name or None}
        if product is None:
            action = "create"
        elif before and before["name"] == name and (before["categoryName"] or "").casefold() == category_name.casefold():
            action = "unchanged"
        else:
            action = "update"
        changes.append(_row_change(row=row, key=sku, action=action, before=before, after=after))
        snapshot.append({"type": "product", "sku": sku, "value": before})
        plan.append({"row": row, "product": product, "category": category, "action": action})
    return issues, changes, snapshot, plan


def _inspect_suppliers(*, rows, organization_id):
    issues, changes, snapshot, plan = [], [], [], []
    keys = {row.get("name", "").casefold() for row in rows}
    suppliers = {
        supplier.name.casefold(): supplier
        for supplier in Supplier.objects.select_for_update().filter(organization_id=organization_id)
        if supplier.name.casefold() in keys
    }
    seen = set()
    for row in rows:
        name = row.get("name", "").strip()
        key = name.casefold()
        row_number = row.get("rowNumber")
        if not name or len(name) > 160:
            issues.append({"rowNumber": row_number, "code": "supplier_name_invalid", "field": "Nome", "message": "Informe o nome do fornecedor (até 160 caracteres)."})
        if key in seen:
            issues.append({"rowNumber": row_number, "code": "duplicate_supplier_in_file", "field": "Nome", "message": f"O fornecedor {name} aparece mais de uma vez no arquivo."})
        seen.add(key)
        email = row.get("email", "")
        if email:
            try:
                validate_email(email)
            except ValidationError:
                issues.append({"rowNumber": row_number, "code": "supplier_email_invalid", "field": "E-mail", "message": "Informe um endereço de e-mail válido ou deixe o campo vazio."})
        if len(row.get("contactName", "")) > 160 or len(email) > 254 or len(row.get("phone", "")) > 32 or len(row.get("notes", "")) > 1000:
            issues.append({"rowNumber": row_number, "code": "supplier_field_too_long", "message": "Um campo do fornecedor excede o limite de caracteres."})
        supplier = suppliers.get(key)
        if supplier and not supplier.is_active:
            issues.append({"rowNumber": row_number, "code": "supplier_inactive", "message": f"O fornecedor {name} está inativo."})
        before = _supplier_snapshot(supplier)
        after = {"name": supplier.name if supplier else name, "contactName": row.get("contactName", ""), "email": email, "phone": row.get("phone", ""), "notes": row.get("notes", "")}
        action = "create" if supplier is None else ("unchanged" if all(before.get(field) == value for field, value in after.items()) else "update")
        changes.append(_row_change(row=row, key=name, action=action, before=before, after=after))
        snapshot.append({"type": "supplier", "key": key, "value": before})
        plan.append({"row": row, "supplier": supplier, "action": action})
    return issues, changes, snapshot, plan


def _inspect_conditions(*, rows, organization_id):
    issues, changes, snapshot, plan = [], [], [], []
    product_skus = {row.get("sku", "") for row in rows}
    products = {
        product.sku: product
        for product in Product.objects.select_for_update().filter(organization_id=organization_id, sku__in=product_skus).select_related("category")
    }
    supplier_keys = {row.get("supplier", "").casefold() for row in rows}
    suppliers = {
        supplier.name.casefold(): supplier
        for supplier in Supplier.objects.select_for_update().filter(organization_id=organization_id)
        if supplier.name.casefold() in supplier_keys
    }
    store_keys = {row.get("store", "").casefold() for row in rows if row.get("store")}
    stores = {
        store.name.casefold(): store
        for store in Store.objects.select_for_update().filter(organization_id=organization_id, is_active=True)
        if store.name.casefold() in store_keys
    }
    seen = set()
    for row in rows:
        row_number = row.get("rowNumber")
        sku = row.get("sku", "")
        supplier_name = row.get("supplier", "")
        store_name = row.get("store", "")
        product = products.get(sku)
        supplier = suppliers.get(supplier_name.casefold())
        store = stores.get(store_name.casefold()) if store_name else None
        if not product:
            issues.append({"rowNumber": row_number, "code": "product_unavailable", "field": "SKU", "message": f"O SKU {sku} não existe nesta rede."})
        if not supplier or not supplier.is_active:
            issues.append({"rowNumber": row_number, "code": "supplier_unavailable", "field": "Fornecedor", "message": f"O fornecedor {supplier_name} não existe ou está inativo nesta rede."})
        if store_name and not store:
            issues.append({"rowNumber": row_number, "code": "store_unavailable", "field": "Loja", "message": f"A loja {store_name} não existe ou está inativa nesta rede."})
        effective_from = _date_or_issue(row.get("effectiveFrom", ""), row=row, field="Vigente desde", issues=issues)
        purchase_unit_name = row.get("purchaseUnitName", "")
        if not purchase_unit_name or len(purchase_unit_name) > 40:
            issues.append({"rowNumber": row_number, "code": "purchase_unit_invalid", "field": "Unidade de compra", "message": "Informe a unidade de compra com até 40 caracteres."})
        base_units = _decimal_or_issue(row.get("baseUnitsPerPurchaseUnit", ""), row=row, field="Unidades por embalagem", issues=issues, places=3, allow_zero=False)
        price = _decimal_or_issue(row.get("pricePerPurchaseUnit", ""), row=row, field="Preço por embalagem", issues=issues, places=2)
        minimum = _decimal_or_issue(row.get("minimumPurchaseQuantity", ""), row=row, field="Pedido mínimo", issues=issues, places=3)
        multiple = _decimal_or_issue(row.get("purchaseMultiple", ""), row=row, field="Múltiplo de compra", issues=issues, places=3, allow_zero=False)
        try:
            lead_time = int(row.get("deliveryLeadTimeDays", ""))
            if lead_time < 0 or lead_time > 365:
                raise ValueError
        except (TypeError, ValueError):
            lead_time = None
            issues.append({"rowNumber": row_number, "code": "lead_time_invalid", "field": "Prazo (dias corridos)", "message": "Informe um prazo inteiro entre 0 e 365 dias corridos."})

        if not (product and supplier and effective_from and base_units is not None and price is not None and minimum is not None and multiple is not None and lead_time is not None):
            continue
        if product.unit == Product.Unit.UNIT and base_units != base_units.to_integral_value():
            issues.append({"rowNumber": row_number, "code": "conversion_invalid", "field": "Unidades por embalagem", "message": "Produtos contáveis precisam de conversão inteira entre embalagem e unidade-base."})
        scope_store_id = store.pk if store else None
        scope_key = (supplier.pk, product.pk, str(scope_store_id or "network"), effective_from.isoformat())
        if scope_key in seen:
            issues.append({"rowNumber": row_number, "code": "duplicate_condition_in_file", "message": "A mesma condição aparece mais de uma vez no arquivo."})
        seen.add(scope_key)

        conditions = SupplierProductCondition.objects.select_for_update().filter(
            organization_id=organization_id,
            supplier_id=supplier.pk,
            product_id=product.pk,
            store_id=scope_store_id,
        )
        existing = conditions.filter(effective_from=effective_from).first()
        latest = conditions.aggregate(latest=Max("effective_from"))["latest"]
        before = _condition_snapshot(existing) if existing else None
        row_values = {
            "purchaseUnitName": purchase_unit_name,
            "baseUnitsPerPurchaseUnit": str(base_units),
            "pricePerPurchaseUnit": str(price),
            "deliveryLeadTimeDays": lead_time,
            "minimumPurchaseQuantity": str(minimum),
            "purchaseMultiple": str(multiple),
        }
        after = {**row_values, "sku": sku, "supplier": supplier.name, "store": store.name if store else None, "effectiveFrom": effective_from.isoformat()}
        if existing:
            same = (
                existing.is_available
                and existing.purchase_unit_name == purchase_unit_name
                and existing.base_units_per_purchase_unit == base_units
                and existing.price_per_purchase_unit == price
                and existing.delivery_lead_time_days == lead_time
                and existing.minimum_purchase_quantity == minimum
                and existing.purchase_multiple == multiple
            )
            action = "unchanged" if same else "conflict"
            if not same:
                issues.append({"rowNumber": row_number, "code": "condition_version_conflict", "message": "Já existe uma condição diferente para este escopo e data de vigência; condições confirmadas são imutáveis."})
        elif latest is not None and effective_from <= latest:
            action = "conflict"
            issues.append({"rowNumber": row_number, "code": "condition_effective_date_conflict", "message": "A nova condição precisa ter vigência posterior à última condição desse escopo."})
        else:
            action = "create"
        snapshot.append({"type": "condition", "key": list(scope_key), "existing": before, "latest": latest.isoformat() if latest else None})
        changes.append(_row_change(row=row, key=f"{sku} / {supplier.name} / {store.name if store else 'rede'}", action=action, before=before, after=after))
        plan.append({"row": row, "supplier": supplier, "product": product, "store": store, "effectiveFrom": effective_from, "baseUnits": base_units, "price": price, "minimum": minimum, "multiple": multiple, "leadTime": lead_time, "action": action})
    return issues, changes, snapshot, plan


def _store_context(*, metadata, organization_id, actor, issues):
    try:
        store = Store.objects.select_for_update().get(
            organization_id=organization_id,
            pk=metadata.get("storeId"),
            is_active=True,
        )
    except (Store.DoesNotExist, ValueError, TypeError, ValidationError):
        issues.append({"rowNumber": None, "code": "store_unavailable", "field": "storeId", "message": "Selecione uma loja ativa da rede."})
        return None
    return store


def _inspect_opening_stock(*, rows, metadata, organization_id, actor):
    issues, changes, snapshot, plan = [], [], [], []
    store = _store_context(metadata=metadata, organization_id=organization_id, actor=actor, issues=issues)
    closed_date = _date_or_issue(metadata.get("closedOperationalDate", ""), row={}, field="Data da contagem", issues=issues)
    if not store or not closed_date:
        return issues, changes, snapshot, plan
    try:
        current_date, _ = current_operational_date(organization_id=organization_id, store=store)
        if closed_date > current_date:
            issues.append({"rowNumber": None, "code": "opening_date_in_future", "field": "closedOperationalDate", "message": "A data fechada não pode ser futura no calendário da loja."})
        resolve_operational_calendar(organization_id=organization_id, operational_date=closed_date, store=store)
    except OperationalCalendarMissing:
        issues.append({"rowNumber": None, "code": "operational_calendar_required", "field": "closedOperationalDate", "message": "Configure o calendário operacional da loja antes de importar o estoque."})
    existing_batch = OpeningStockBatch.objects.select_for_update().filter(organization_id=organization_id, store=store).first()
    if existing_batch:
        issues.append({"rowNumber": None, "code": "opening_stock_already_recorded", "message": "O estoque inicial desta loja já foi registrado."})
    expected_assortments = list(
        StoreProduct.objects.select_for_update().filter(
            organization_id=organization_id,
            store=store,
            product__is_active=True,
            effective_from__lte=closed_date,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=closed_date)).select_related("product")
    )
    expected_by_sku = {assortment.product.sku: assortment.product for assortment in expected_assortments}
    seen = set()
    for row in rows:
        sku = row.get("sku", "")
        if sku in seen:
            issues.append({"rowNumber": row.get("rowNumber"), "code": "duplicate_sku_in_file", "field": "SKU", "message": f"O SKU {sku} aparece mais de uma vez no arquivo."})
        seen.add(sku)
        product = expected_by_sku.get(sku)
        if not product:
            issues.append({"rowNumber": row.get("rowNumber"), "code": "product_not_in_assortment", "field": "SKU", "message": f"O SKU {sku} não está ativo no sortimento da loja na data da contagem."})
            continue
        quantity = _decimal_or_issue(row.get("quantity", ""), row=row, field="Quantidade", issues=issues)
        if quantity is not None and not _quantity_valid_for_unit(quantity, product.unit):
            issues.append({"rowNumber": row.get("rowNumber"), "code": "quantity_precision_invalid", "field": "Quantidade", "message": "Unidades contáveis aceitam apenas inteiros; kg/litro aceitam até três casas decimais."})
        if quantity is not None:
            plan.append({"row": row, "product": product, "quantity": quantity})
            changes.append(_row_change(row=row, key=sku, action="create", after={"quantity": str(quantity), "unit": product.unit}))
    missing = sorted(set(expected_by_sku) - seen)
    if missing:
        issues.append({"rowNumber": None, "code": "opening_stock_incomplete", "field": "SKU", "message": "Inclua todos os produtos ativos do sortimento, inclusive os que estão com quantidade zero. SKUs ausentes: " + ", ".join(missing[:50]) + ("…" if len(missing) > 50 else "")})
    snapshot.append({"type": "opening", "storeId": str(store.pk), "closedDate": closed_date.isoformat(), "existingBatch": str(existing_batch.pk) if existing_batch else None, "assortments": [{"id": str(item.pk), "product": _product_snapshot(item.product), "from": item.effective_from.isoformat(), "to": item.effective_to.isoformat() if item.effective_to else None} for item in expected_assortments]})
    return issues, changes, snapshot, plan


def _inspect_daily_sales(*, rows, metadata, organization_id, actor):
    issues, changes, snapshot, plan = [], [], [], []
    store = _store_context(metadata=metadata, organization_id=organization_id, actor=actor, issues=issues)
    date_from = _date_or_issue(metadata.get("operationalDateFrom", ""), row={}, field="Período inicial", issues=issues)
    date_to = _date_or_issue(metadata.get("operationalDateTo", ""), row={}, field="Período final", issues=issues)
    if not store or not date_from or not date_to:
        return issues, changes, snapshot, plan
    if date_from > date_to:
        issues.append({"rowNumber": None, "code": "sales_period_invalid", "message": "A data inicial deve ser anterior ou igual à data final."})
    if (date_to - date_from).days > 365:
        issues.append({"rowNumber": None, "code": "sales_period_too_long", "message": "Uma importação pode declarar no máximo 366 dias operacionais."})
    try:
        current_date, _ = current_operational_date(organization_id=organization_id, store=store)
        if date_to > current_date:
            issues.append({"rowNumber": None, "code": "sales_date_in_future", "message": "O período completo de vendas não pode terminar em uma data futura."})
        resolve_operational_calendar(organization_id=organization_id, operational_date=date_from, store=store)
        resolve_operational_calendar(organization_id=organization_id, operational_date=date_to, store=store)
    except OperationalCalendarMissing:
        issues.append({"rowNumber": None, "code": "operational_calendar_required", "message": "Configure o calendário operacional da loja antes de importar vendas."})
    if not OpeningStockBatch.objects.select_for_update().filter(organization_id=organization_id, store=store).exists():
        issues.append({"rowNumber": None, "code": "opening_stock_required", "message": "Registre o estoque inicial da loja antes de importar vendas."})

    existing_coverage = list(DailySalesCoverage.objects.select_for_update().filter(
        organization_id=organization_id,
        store=store,
        operational_date_from__lte=date_to,
        operational_date_to__gte=date_from,
    ))
    if existing_coverage:
        issues.append({"rowNumber": None, "code": "sales_coverage_overlap", "message": "O período se sobrepõe a uma faixa já declarada como completa. Escolha um período sem sobreposição."})

    assortments = list(StoreProduct.objects.select_for_update().filter(
        organization_id=organization_id,
        store=store,
        effective_from__lte=date_to,
    ).select_related("product"))
    assortment_by_product = {}
    for assortment in assortments:
        assortment_by_product.setdefault(assortment.product_id, []).append(assortment)
    products_by_sku = {assortment.product.sku: assortment.product for assortment in assortments}
    seen = set()
    row_plan = []
    for row in rows:
        row_number = row.get("rowNumber")
        sku = row.get("sku", "")
        product = products_by_sku.get(sku)
        operational_date = _date_or_issue(row.get("operationalDate", ""), row=row, field="Data", issues=issues)
        quantity = _decimal_or_issue(row.get("quantity", ""), row=row, field="Quantidade", issues=issues)
        if not product:
            issues.append({"rowNumber": row_number, "code": "product_unavailable", "field": "SKU", "message": f"O SKU {sku} não pertence ao sortimento da loja no período informado."})
        if operational_date and (operational_date < date_from or operational_date > date_to):
            issues.append({"rowNumber": row_number, "code": "sales_date_outside_period", "field": "Data", "message": "A data precisa estar dentro do período declarado como completo."})
        if product and operational_date:
            has_history = StoreProduct.objects.filter(organization_id=organization_id, store=store, product=product, effective_from__lte=operational_date).exists()
            if not has_history:
                issues.append({"rowNumber": row_number, "code": "product_not_assorted", "field": "SKU", "message": f"O SKU {sku} ainda não fazia parte do sortimento na data informada."})
        if product and quantity is not None and not _quantity_valid_for_unit(quantity, product.unit):
            issues.append({"rowNumber": row_number, "code": "quantity_precision_invalid", "field": "Quantidade", "message": "Unidades contáveis aceitam apenas inteiros; kg/litro aceitam até três casas decimais."})
        key = (product.pk if product else sku, operational_date)
        if key in seen:
            issues.append({"rowNumber": row_number, "code": "duplicate_sale_in_file", "message": f"O SKU {sku} aparece mais de uma vez para a mesma loja e data."})
        seen.add(key)
        if product and operational_date and quantity is not None:
            row_plan.append({"row": row, "product": product, "operationalDate": operational_date, "quantity": quantity})
            changes.append(_row_change(row=row, key=f"{sku} / {operational_date.isoformat()}", action="create", after={"quantity": str(quantity), "unit": product.unit}))

    existing_sales = list(DailyProductSale.objects.select_for_update().filter(
        organization_id=organization_id,
        store=store,
        operational_date__gte=date_from,
        operational_date__lte=date_to,
    ).select_related("product"))
    existing_keys = {(sale.product_id, sale.operational_date): sale for sale in existing_sales}
    explicit_keys = {(item["product"].pk, item["operationalDate"]) for item in row_plan}
    for item in row_plan:
        existing = existing_keys.get((item["product"].pk, item["operationalDate"]))
        if existing:
            issues.append({"rowNumber": item["row"].get("rowNumber"), "code": "daily_sale_already_recorded", "message": f"Já existe venda registrada para o SKU {item['product'].sku} nessa data. Corrija ou remova o lançamento existente antes de importar."})
    for key, existing in existing_keys.items():
        if key not in explicit_keys and existing.quantity != 0:
            active_on_date = any(
                assortment.effective_from <= existing.operational_date
                and (assortment.effective_to is None or existing.operational_date < assortment.effective_to)
                for assortment in assortment_by_product.get(existing.product_id, [])
            )
            if active_on_date:
                issues.append({"rowNumber": None, "code": "implicit_zero_conflict", "message": f"A planilha declara {existing.product.sku} sem vendas em {existing.operational_date.strftime('%d/%m/%Y')}, mas já existe um total registrado. Resolva esse dado antes de importar."})

    coverage_snapshot = [{"id": str(row.pk), "from": row.operational_date_from.isoformat(), "to": row.operational_date_to.isoformat()} for row in existing_coverage]
    snapshot.append({
        "type": "daily_sales",
        "storeId": str(store.pk),
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "coverage": coverage_snapshot,
        "assortments": [{"id": str(item.pk), "product": _product_snapshot(item.product), "from": item.effective_from.isoformat(), "to": item.effective_to.isoformat() if item.effective_to else None} for item in assortments],
        "existingSales": [{"id": str(item.pk), "productId": str(item.product_id), "sku": item.product.sku, "date": item.operational_date.isoformat(), "quantity": str(item.quantity)} for item in existing_sales],
    })
    plan.extend(row_plan)
    plan.append({"coverage": (date_from, date_to), "store": store})
    return issues, changes, snapshot, plan


def _inspect_out_of_stock_days(*, rows, metadata, organization_id, actor):
    issues, changes, snapshot, plan = [], [], [], []
    store = _store_context(metadata=metadata, organization_id=organization_id, actor=actor, issues=issues)
    if not store:
        return issues, changes, snapshot, plan
    try:
        current_date, _ = current_operational_date(organization_id=organization_id, store=store)
        for row in rows:
            day = _date_or_issue(row.get("operationalDate", ""), row=row, field="Data", issues=issues)
            if day and day > current_date:
                issues.append({"rowNumber": row.get("rowNumber"), "code": "out_of_stock_date_in_future", "field": "Data", "message": "Não é possível registrar um dia futuro sem estoque."})
            if day:
                try:
                    resolve_operational_calendar(organization_id=organization_id, operational_date=day, store=store)
                except OperationalCalendarMissing:
                    issues.append({"rowNumber": row.get("rowNumber"), "code": "operational_calendar_required", "field": "Data", "message": "Configure o calendário operacional da loja para esta data."})
    except OperationalCalendarMissing:
        issues.append({"rowNumber": None, "code": "operational_calendar_required", "message": "Configure o calendário operacional da loja antes de importar dias sem estoque."})
    products = {product.sku: product for product in Product.objects.select_for_update().filter(organization_id=organization_id)}
    seen, target_keys = set(), []
    assortment_snapshot = []
    for row in rows:
        sku = row.get("sku", "")
        product = products.get(sku)
        operational_date = _date_or_issue(row.get("operationalDate", ""), row=row, field="Data", issues=issues)
        if not product:
            issues.append({"rowNumber": row.get("rowNumber"), "code": "product_unavailable", "field": "SKU", "message": f"O SKU {sku} não existe nesta rede."})
        assortments_for_day = []
        if product and operational_date:
            assortments_for_day = list(StoreProduct.objects.select_for_update().filter(
                organization_id=organization_id,
                store=store,
                product=product,
                effective_from__lte=operational_date,
            ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=operational_date)))
            if not assortments_for_day:
                issues.append({"rowNumber": row.get("rowNumber"), "code": "product_not_assorted", "field": "SKU", "message": f"O SKU {sku} não fazia parte do sortimento nessa data."})
        assortment_snapshot.append({
            "sku": sku,
            "product": _product_snapshot(product),
            "date": operational_date.isoformat() if operational_date else None,
            "assortments": [{"id": str(item.pk), "from": item.effective_from.isoformat(), "to": item.effective_to.isoformat() if item.effective_to else None} for item in assortments_for_day],
        })
        key = (product.pk if product else sku, operational_date)
        if key in seen:
            issues.append({"rowNumber": row.get("rowNumber"), "code": "duplicate_out_of_stock_day", "message": f"O SKU {sku} aparece mais de uma vez para a mesma data."})
        seen.add(key)
        if product and operational_date:
            target_keys.append((row, product, operational_date))
            plan.append({"row": row, "product": product, "operationalDate": operational_date})
            changes.append(_row_change(row=row, key=f"{sku} / {operational_date.isoformat()}", action="create"))
    existing = list(DailyStockAvailability.objects.select_for_update().filter(
        organization_id=organization_id,
        store=store,
        product_id__in=[product.pk for _, product, _ in target_keys],
        operational_date__in=[day for _, _, day in target_keys],
    ))
    existing_keys = {(item.product_id, item.operational_date) for item in existing}
    for row, product, day in target_keys:
        if (product.pk, day) in existing_keys:
            issues.append({"rowNumber": row.get("rowNumber"), "code": "out_of_stock_day_already_recorded", "message": f"Já existe registro sem estoque para {product.sku} nessa data."})
    snapshot.append({"type": "out_of_stock_days", "storeId": str(store.pk), "targets": assortment_snapshot, "existing": [{"productId": str(item.product_id), "date": item.operational_date.isoformat()} for item in existing]})
    return issues, changes, snapshot, plan


def inspect_batch(*, kind, rows, metadata, organization_id, actor):
    inspect = {
        ImportBatch.Kind.PRODUCTS: lambda: _inspect_products(rows=rows, organization_id=organization_id),
        ImportBatch.Kind.SUPPLIERS: lambda: _inspect_suppliers(rows=rows, organization_id=organization_id),
        ImportBatch.Kind.CONDITIONS: lambda: _inspect_conditions(rows=rows, organization_id=organization_id),
        ImportBatch.Kind.OPENING_STOCK: lambda: _inspect_opening_stock(rows=rows, metadata=metadata, organization_id=organization_id, actor=actor),
        ImportBatch.Kind.DAILY_SALES: lambda: _inspect_daily_sales(rows=rows, metadata=metadata, organization_id=organization_id, actor=actor),
        ImportBatch.Kind.OUT_OF_STOCK_DAYS: lambda: _inspect_out_of_stock_days(rows=rows, metadata=metadata, organization_id=organization_id, actor=actor),
    }.get(kind)
    if inspect is None:
        raise ValueError("Tipo de importação não reconhecido.")
    issues, changes, snapshot, plan = inspect()
    counts = {"create": 0, "update": 0, "unchanged": 0}
    for change in changes:
        if change["action"] in counts:
            counts[change["action"]] += 1
    counts["rows"] = len(rows)
    preview = {
        "counts": counts,
        "issueCount": len(issues),
        "issues": issues[:MAX_ISSUES],
        "changeCount": len(changes),
        "changes": changes[:MAX_CHANGES],
        "canConfirm": not issues,
    }
    return preview, _snapshot_fingerprint(snapshot), plan


def create_preview(*, request, kind, rows, metadata, file_name, file_sha256):
    organization_id = set_active_organization_rls_context(user=request.user, session=request._request.session)
    ImportBatch.objects.filter(
        organization_id=organization_id,
        status=ImportBatch.Status.PREVIEW,
        expires_at__lte=timezone.now(),
    ).update(status=ImportBatch.Status.EXPIRED, rows=[], metadata={})
    preview, fingerprint, _ = inspect_batch(
        kind=kind, rows=rows, metadata=metadata, organization_id=organization_id, actor=request.user
    )
    batch = ImportBatch.objects.create(
        organization_id=organization_id,
        created_by=request.user,
        kind=kind,
        file_name=file_name,
        file_sha256=file_sha256,
        rows=rows,
        metadata=metadata,
        preview=preview,
        target_fingerprint=fingerprint,
        expires_at=timezone.now() + PREVIEW_TTL,
    )
    return batch


def _apply_products(*, plan, organization_id):
    category_map = {
        item.name.casefold(): item
        for item in ProductCategory.objects.select_for_update().filter(organization_id=organization_id)
    }
    for item in plan:
        row, product = item["row"], item["product"]
        category_name = row.get("category", "")
        category = category_map.get(category_name.casefold()) if category_name else None
        if category_name and category is None:
            category = ProductCategory.objects.create(organization_id=organization_id, name=category_name)
            category_map[category_name.casefold()] = category
        if item["action"] == "create":
            Product.objects.create(
                organization_id=organization_id,
                sku=row["sku"],
                name=row["name"],
                unit=row["unit"],
                category=category,
            )
        elif item["action"] == "update":
            product.name = row["name"]
            product.category = category
            product.save(update_fields=["name", "category", "updated_at"])


def _apply_suppliers(*, plan, organization_id, actor):
    for item in plan:
        if item["action"] == "unchanged":
            continue
        row, supplier = item["row"], item["supplier"]
        values = {
            "contact_name": row.get("contactName", ""),
            "email": row.get("email", ""),
            "phone": row.get("phone", ""),
            "notes": row.get("notes", ""),
        }
        if supplier is None:
            Supplier.objects.create(
                organization_id=organization_id,
                name=row["name"],
                created_by=actor,
                updated_by=actor,
                **values,
            )
        else:
            for field, value in values.items():
                setattr(supplier, field, value)
            supplier.updated_by = actor
            supplier.save(update_fields=[*values.keys(), "updated_by", "updated_at"])


def _apply_conditions(*, plan, organization_id, actor):
    for item in plan:
        if item["action"] != "create":
            continue
        SupplierProductCondition.objects.create(
            organization_id=organization_id,
            supplier=item["supplier"],
            product=item["product"],
            store=item["store"],
            effective_from=item["effectiveFrom"],
            purchase_unit_name=item["row"]["purchaseUnitName"],
            base_units_per_purchase_unit=item["baseUnits"],
            price_per_purchase_unit=item["price"],
            delivery_lead_time_days=item["leadTime"],
            minimum_purchase_quantity=item["minimum"],
            purchase_multiple=item["multiple"],
            created_by=actor,
        )


def apply_batch(*, request, batch, organization_id, plan):
    if batch.kind == ImportBatch.Kind.PRODUCTS:
        _apply_products(plan=plan, organization_id=organization_id)
    elif batch.kind == ImportBatch.Kind.SUPPLIERS:
        _apply_suppliers(plan=plan, organization_id=organization_id, actor=request.user)
    elif batch.kind == ImportBatch.Kind.CONDITIONS:
        _apply_conditions(plan=plan, organization_id=organization_id, actor=request.user)
    elif batch.kind == ImportBatch.Kind.OPENING_STOCK:
        closed_date = parse_operational_date(batch.metadata["closedOperationalDate"])
        confirm_opening_stock(
            request=request,
            store_id=batch.metadata["storeId"],
            closed_operational_date=closed_date,
            lines=[{"product_id": item["product"].pk, "quantity": item["quantity"]} for item in plan],
            source=OpeningStockBatch.Source.SPREADSHEET,
        )
    elif batch.kind == ImportBatch.Kind.DAILY_SALES:
        coverage = next(item["coverage"] for item in plan if "coverage" in item)
        store = next(item["store"] for item in plan if "coverage" in item)
        for item in plan:
            if "coverage" in item:
                continue
            operation_id = uuid.uuid5(batch.pk, f"{item['product'].pk}:{item['operationalDate'].isoformat()}")
            create_daily_sale(
                request=request,
                store_id=store.pk,
                product_id=item["product"].pk,
                operational_date=item["operationalDate"],
                quantity=item["quantity"],
                operation_id=operation_id,
                source=DailyProductSale.Source.SPREADSHEET,
            )
        DailySalesCoverage.objects.create(
            organization_id=organization_id,
            store=store,
            operational_date_from=coverage[0],
            operational_date_to=coverage[1],
            created_by=request.user,
        )
    elif batch.kind == ImportBatch.Kind.OUT_OF_STOCK_DAYS:
        for item in plan:
            DailyStockAvailability.objects.create(
                organization_id=organization_id,
                store_id=batch.metadata["storeId"],
                product=item["product"],
                operational_date=item["operationalDate"],
                created_by=request.user,
            )


def confirm_batch(*, request, batch_id):
    organization_id = set_active_organization_rls_context(user=request.user, session=request._request.session)
    pending_error = None
    confirmed_batch = None
    with transaction.atomic():
        try:
            batch = ImportBatch.objects.select_for_update().get(
                organization_id=organization_id,
                created_by=request.user,
                pk=batch_id,
            )
        except ImportBatch.DoesNotExist as exc:
            raise ImportUnavailable from exc
        if batch.status == ImportBatch.Status.CONFIRMED:
            pending_error = ImportNoLongerConfirmable(code="import_already_confirmed", message="Esta importação já foi confirmada.")
        elif batch.status != ImportBatch.Status.PREVIEW or batch.expires_at <= timezone.now():
            batch.status = ImportBatch.Status.EXPIRED
            batch.rows = []
            batch.metadata = {}
            batch.save(update_fields=["status", "rows", "metadata"])
            pending_error = ImportNoLongerConfirmable(code="import_preview_expired", message="A prévia expirou. Envie novamente o arquivo para gerar uma prévia atualizada.", status=410)
        else:
            preview, fingerprint, plan = inspect_batch(
                kind=batch.kind,
                rows=batch.rows,
                metadata=batch.metadata,
                organization_id=organization_id,
                actor=request.user,
            )
            if fingerprint != batch.target_fingerprint:
                batch.preview = preview
                batch.target_fingerprint = fingerprint
                batch.expires_at = timezone.now() + PREVIEW_TTL
                batch.save(update_fields=["preview", "target_fingerprint", "expires_at"])
                pending_error = ImportNoLongerConfirmable(
                    code="import_preview_stale",
                    message="Os dados mudaram desde a prévia. Revise a prévia atualizada e confirme novamente.",
                    preview=serialize_batch(batch),
                )
            elif not preview["canConfirm"]:
                batch.preview = preview
                batch.save(update_fields=["preview"])
                pending_error = ImportNoLongerConfirmable(
                    code="import_validation_failed",
                    message="A validação encontrou conflitos. Nenhuma linha foi gravada; corrija os dados e envie o arquivo novamente.",
                    preview=serialize_batch(batch),
                )
            else:
                try:
                    with transaction.atomic():
                        apply_batch(request=request, batch=batch, organization_id=organization_id, plan=plan)
                except (IntegrityError, DailySaleAlreadyRecorded, IdempotencyConflict, OpeningStockAlreadyRecorded) as exc:
                    fresh_preview, fresh_fingerprint, _ = inspect_batch(
                        kind=batch.kind,
                        rows=batch.rows,
                        metadata=batch.metadata,
                        organization_id=organization_id,
                        actor=request.user,
                    )
                    batch.preview = fresh_preview
                    batch.target_fingerprint = fresh_fingerprint
                    batch.expires_at = timezone.now() + PREVIEW_TTL
                    batch.save(update_fields=["preview", "target_fingerprint", "expires_at"])
                    pending_error = ImportNoLongerConfirmable(
                        code="import_preview_stale",
                        message="Os dados mudaram durante a confirmação. Nenhuma linha foi gravada; gere e revise uma nova prévia.",
                        preview={"batchId": str(batch.pk), "kind": batch.kind, "fileName": batch.file_name, "status": batch.status, **fresh_preview},
                    )
                except (
                    InvalidOpeningStock,
                    OpeningDateInFuture,
                    OpeningProductNotAssorted,
                    OpeningProductUnavailable,
                    OpeningStoreUnavailable,
                    InvalidDailySale,
                    DailySaleDateInFuture,
                    SalesOpeningStockRequired,
                    SalesProductNotAssorted,
                    SalesProductUnavailable,
                    SalesStoreUnavailable,
                    OperationalCalendarMissing,
                ) as exc:
                    fresh_preview, fresh_fingerprint, _ = inspect_batch(
                        kind=batch.kind,
                        rows=batch.rows,
                        metadata=batch.metadata,
                        organization_id=organization_id,
                        actor=request.user,
                    )
                    batch.preview = fresh_preview
                    batch.target_fingerprint = fresh_fingerprint
                    batch.expires_at = timezone.now() + PREVIEW_TTL
                    batch.save(update_fields=["preview", "target_fingerprint", "expires_at"])
                    pending_error = ImportNoLongerConfirmable(
                        code="import_validation_failed",
                        message="A validação final falhou. Nenhuma linha foi gravada; revise o arquivo e gere outra prévia.",
                        preview={"batchId": str(batch.pk), "kind": batch.kind, "fileName": batch.file_name, "status": batch.status, **fresh_preview},
                    )
                else:
                    batch.status = ImportBatch.Status.CONFIRMED
                    batch.confirmed_at = timezone.now()
                    batch.rows = []
                    batch.metadata = {}
                    batch.save(update_fields=["status", "confirmed_at", "rows", "metadata"])
                    confirmed_batch = batch
    if pending_error is not None:
        raise pending_error
    return confirmed_batch


def serialize_batch(batch):
    return {
        "batchId": str(batch.pk),
        "kind": batch.kind,
        "fileName": batch.file_name,
        "fileSha256": batch.file_sha256,
        "status": batch.status,
        "createdAt": batch.created_at,
        "expiresAt": batch.expires_at,
        "confirmedAt": batch.confirmed_at,
        **batch.preview,
    }
