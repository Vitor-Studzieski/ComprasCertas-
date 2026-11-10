"""Effective operational-calendar resolution and immutable version creation."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from apps.identity.models import Organization
from apps.inventory.models import (
    OperationalCalendarDefault,
    OpeningStockBatch,
    Store,
    StoreOperationalCalendarOverride,
    StockMovement,
)


class OperationalCalendarMissing(Exception):
    """No complete operational calendar is effective for the requested date."""


class CalendarVersionConflict(Exception):
    """A calendar version would alter an earlier effective period."""


@dataclass(frozen=True)
class EffectiveOperationalCalendar:
    time_zone: str
    operational_day_start: time


def is_valid_time_zone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return False
    return True


def resolve_operational_calendar(
    *, organization_id, operational_date: date, store: Store | None = None
) -> EffectiveOperationalCalendar:
    """Resolve the latest effective network defaults and optional store fields."""
    defaults = (
        OperationalCalendarDefault.objects.filter(
            organization_id=organization_id,
            effective_from__lte=operational_date,
        )
        .order_by("-effective_from", "-id")
        .first()
    )
    override = None
    if store is not None:
        override = (
            StoreOperationalCalendarOverride.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
                effective_from__lte=operational_date,
            )
            .order_by("-effective_from", "-id")
            .first()
        )

    time_zone = (
        override.time_zone if override and override.time_zone else None
    ) or (defaults.time_zone if defaults else None)
    day_start = (
        override.operational_day_start
        if override and override.operational_day_start is not None
        else None
    ) or (defaults.operational_day_start if defaults else None)
    if not time_zone or day_start is None:
        raise OperationalCalendarMissing

    return EffectiveOperationalCalendar(
        time_zone=time_zone,
        operational_day_start=day_start,
    )


def current_operational_date(*, organization_id, store: Store | None, at: datetime | None = None):
    """Get the store-local operational date, applying that date's calendar."""
    instant = at or timezone.now()
    if timezone.is_naive(instant):
        instant = timezone.make_aware(instant, datetime_timezone.utc)
    utc_date = instant.astimezone(datetime_timezone.utc).date()

    # Local dates differ from UTC by at most one day for IANA civil zones. Try
    # nearby dates and keep only a policy that is also effective on the local
    # operational date it computes. This handles a first policy effective on
    # the next local date when UTC is still on the previous date.
    for offset in (0, 1, -1, 2, -2):
        candidate_date = utc_date + timedelta(days=offset)
        try:
            calendar = resolve_operational_calendar(
                organization_id=organization_id,
                operational_date=candidate_date,
                store=store,
            )
        except OperationalCalendarMissing:
            continue

        local_now = instant.astimezone(ZoneInfo(calendar.time_zone))
        operational_date = local_now.date()
        if local_now.timetz().replace(tzinfo=None) < calendar.operational_day_start:
            operational_date -= timedelta(days=1)
        try:
            effective_calendar = resolve_operational_calendar(
                organization_id=organization_id,
                operational_date=operational_date,
                store=store,
            )
        except OperationalCalendarMissing:
            continue
        if effective_calendar == calendar:
            return operational_date, effective_calendar

    raise OperationalCalendarMissing


def create_default_calendar_version(
    *, organization_id, effective_from: date, time_zone: str, operational_day_start: time, actor
):
    if not is_valid_time_zone(time_zone):
        raise ValueError("O fuso horário deve ser um identificador IANA válido.")

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        latest = (
            OperationalCalendarDefault.objects
            .filter(organization_id=organization_id)
            .order_by("-effective_from")
            .first()
        )
        has_operational_data = (
            OpeningStockBatch.objects.filter(organization_id=organization_id).exists()
            or StockMovement.objects.filter(organization_id=organization_id).exists()
        )
        if latest and effective_from <= latest.effective_from:
            raise CalendarVersionConflict
        if latest or has_operational_data:
            effective_dates = []
            try:
                current_date, _ = current_operational_date(
                    organization_id=organization_id,
                    store=None,
                )
                effective_dates.append(current_date)
            except OperationalCalendarMissing:
                if has_operational_data:
                    raise CalendarVersionConflict
            for store in Store.objects.filter(organization_id=organization_id):
                try:
                    store_date, _ = current_operational_date(
                        organization_id=organization_id,
                        store=store,
                    )
                except OperationalCalendarMissing:
                    store_has_stock_data = (
                        OpeningStockBatch.objects.filter(
                            organization_id=organization_id,
                            store_id=store.pk,
                        ).exists()
                        or StockMovement.objects.filter(
                            organization_id=organization_id,
                            store_id=store.pk,
                        ).exists()
                    )
                    if store_has_stock_data:
                        raise CalendarVersionConflict
                    continue
                effective_dates.append(store_date)
            if any(effective_from <= current_date for current_date in effective_dates):
                raise CalendarVersionConflict

        return OperationalCalendarDefault.objects.create(
            organization_id=organization_id,
            effective_from=effective_from,
            time_zone=time_zone,
            operational_day_start=operational_day_start,
            created_by=actor,
        )


def create_store_calendar_override(
    *,
    organization_id,
    store: Store,
    effective_from: date,
    time_zone: str | None,
    operational_day_start: time | None,
    actor,
):
    if time_zone and not is_valid_time_zone(time_zone):
        raise ValueError("O fuso horário deve ser um identificador IANA válido.")

    with transaction.atomic():
        Store.objects.select_for_update().get(
            organization_id=organization_id,
            pk=store.pk,
        )
        latest = (
            StoreOperationalCalendarOverride.objects
            .filter(organization_id=organization_id, store_id=store.pk)
            .order_by("-effective_from")
            .first()
        )
        if latest and effective_from <= latest.effective_from:
            raise CalendarVersionConflict
        has_store_data = (
            OpeningStockBatch.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).exists()
            or StockMovement.objects.filter(
                organization_id=organization_id,
                store_id=store.pk,
            ).exists()
        )
        if latest or has_store_data:
            try:
                current_date, _ = current_operational_date(
                    organization_id=organization_id,
                    store=store,
                )
            except OperationalCalendarMissing:
                if has_store_data:
                    raise CalendarVersionConflict
                current_date = None
            if current_date is not None and effective_from <= current_date:
                raise CalendarVersionConflict

        return StoreOperationalCalendarOverride.objects.create(
            organization_id=organization_id,
            store=store,
            effective_from=effective_from,
            time_zone=time_zone,
            operational_day_start=operational_day_start,
            created_by=actor,
        )
