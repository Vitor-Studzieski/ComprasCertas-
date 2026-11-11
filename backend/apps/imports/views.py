"""Authenticated spreadsheet upload, preview, and atomic confirmation endpoints."""

import csv
import io
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from openpyxl import Workbook
from rest_framework.parsers import FormParser, MultiPartParser
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

from apps.identity.models import Membership, StoreAccessAssignment
from apps.identity.services.organization_context import (
    ActiveOrganizationRequired,
    set_active_organization_rls_context,
)
from apps.imports.models import ImportBatch
from apps.imports.parsers import HEADERS, InvalidSpreadsheet, parse_upload
from apps.imports.services import (
    ImportNoLongerConfirmable,
    ImportUnavailable,
    confirm_batch,
    create_preview,
    inspect_batch,
    serialize_batch,
)


def _problem(code, message, status, *, fields=None, preview=None):
    body = {"code": code, "message": message}
    if fields is not None:
        body["fields"] = fields
    if preview is not None:
        body["preview"] = preview
    return Response(body, status=status)


def _active_organization(request):
    try:
        return set_active_organization_rls_context(
            user=request.user,
            session=request._request.session,
        ), None
    except ActiveOrganizationRequired:
        return None, _problem(
            "active_organization_required",
            "Selecione uma organização ativa antes de importar dados.",
            HTTP_404_NOT_FOUND,
        )


def _member_for(*, request, organization_id):
    return Membership.objects.select_for_update().filter(
        organization_id=organization_id,
        user_id=request.user.pk,
        status=Membership.Status.ACTIVE,
    ).first()


def _authorize_kind(*, request, organization_id, kind):
    membership = _member_for(request=request, organization_id=organization_id)
    if membership is None:
        return None, _problem("import_access_required", "Você não tem acesso ativo a esta rede.", HTTP_403_FORBIDDEN)
    catalog_kinds = {ImportBatch.Kind.PRODUCTS, ImportBatch.Kind.SUPPLIERS, ImportBatch.Kind.CONDITIONS}
    if kind in catalog_kinds:
        allowed = {Membership.Role.ADMINISTRATOR, Membership.Role.BUYER}
    elif kind in {ImportBatch.Kind.OPENING_STOCK, ImportBatch.Kind.DAILY_SALES, ImportBatch.Kind.OUT_OF_STOCK_DAYS}:
        allowed = {Membership.Role.ADMINISTRATOR, Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR}
    else:
        return None, _problem("import_kind_invalid", "Tipo de importação não reconhecido.", HTTP_400_BAD_REQUEST)
    if membership.role not in allowed:
        return None, _problem("import_access_required", "Seu papel não permite importar esse tipo de planilha.", HTTP_403_FORBIDDEN)
    return membership, None


def _authorize_scope(*, request, organization_id, membership, kind, metadata, plan):
    required_roles = {
        ImportBatch.Kind.CONDITIONS: [Membership.Role.BUYER],
        ImportBatch.Kind.OPENING_STOCK: [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR],
        ImportBatch.Kind.DAILY_SALES: [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR],
        ImportBatch.Kind.OUT_OF_STOCK_DAYS: [Membership.Role.STORE_MANAGER, Membership.Role.OPERATOR],
    }.get(kind, [])
    store_ids = set()
    if metadata.get("storeId"):
        store_ids.add(str(metadata["storeId"]))
    for item in plan:
        store = item.get("store")
        if store is not None:
            store_ids.add(str(store.pk))
    if membership.role == Membership.Role.ADMINISTRATOR or not required_roles:
        return None
    if membership.role not in required_roles:
        return _problem(
            "store_access_required",
            "A importação contém uma loja fora do seu escopo de acesso.",
            HTTP_403_FORBIDDEN,
        )
    for store_id in store_ids:
        try:
            assignment = StoreAccessAssignment.objects.select_for_update().filter(
                organization_id=organization_id,
                membership=membership,
                store_id=store_id,
                revoked_at__isnull=True,
            ).first()
        except (ValueError, TypeError, ValidationError):
            assignment = None
        if assignment is None:
            return _problem(
                "store_access_required",
                "A importação contém uma loja fora do seu escopo de acesso.",
                HTTP_403_FORBIDDEN,
            )
    if not store_ids and kind != ImportBatch.Kind.CONDITIONS:
        return _problem("store_required", "Selecione uma loja para esta importação.", HTTP_400_BAD_REQUEST)
    return None


def _request_date(value, field_name, errors):
    if not value:
        errors[field_name] = ["Este campo é obrigatório."]
        return None
    try:
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        errors[field_name] = ["Informe uma data válida."]
        return None


def _metadata_from_request(request, kind):
    metadata = {}
    errors = {}
    if kind in {ImportBatch.Kind.OPENING_STOCK, ImportBatch.Kind.DAILY_SALES, ImportBatch.Kind.OUT_OF_STOCK_DAYS}:
        store_id = str(request.data.get("storeId", "")).strip()
        if not store_id:
            errors["storeId"] = ["Selecione uma loja."]
        else:
            metadata["storeId"] = store_id
    if kind == ImportBatch.Kind.OPENING_STOCK:
        parsed = _request_date(request.data.get("closedOperationalDate"), "closedOperationalDate", errors)
        if parsed:
            metadata["closedOperationalDate"] = parsed
    if kind == ImportBatch.Kind.DAILY_SALES:
        from_date = _request_date(request.data.get("operationalDateFrom"), "operationalDateFrom", errors)
        to_date = _request_date(request.data.get("operationalDateTo"), "operationalDateTo", errors)
        if from_date:
            metadata["operationalDateFrom"] = from_date
        if to_date:
            metadata["operationalDateTo"] = to_date
    return metadata, errors


@method_decorator(csrf_protect, name="dispatch")
class ImportTemplateView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, kind):
        if kind not in HEADERS:
            return _problem("import_kind_invalid", "Tipo de importação não reconhecido.", HTTP_404_NOT_FOUND)
        file_format = request.query_params.get("format", "csv").lower()
        if file_format == "csv":
            output = io.StringIO(newline="")
            csv.writer(output, delimiter=";", lineterminator="\r\n").writerow(HEADERS[kind])
            response = HttpResponse(output.getvalue().encode("utf-8"), content_type="text/csv; charset=utf-8")
            extension = "csv"
        elif file_format == "xlsx":
            workbook = Workbook(write_only=True)
            worksheet = workbook.create_sheet("Modelo")
            worksheet.append(HEADERS[kind])
            content = io.BytesIO()
            workbook.save(content)
            response = HttpResponse(
                content.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            extension = "xlsx"
        else:
            return _problem("spreadsheet_format_invalid", "Formato do modelo precisa ser csv ou xlsx.", HTTP_400_BAD_REQUEST)
        response["Content-Disposition"] = f'attachment; filename="comprascerta-modelo-{kind}.{extension}"'
        return response


@method_decorator(csrf_protect, name="dispatch")
class ImportPreviewCreateView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        kind = str(request.data.get("kind", ""))
        with transaction.atomic():
            organization_id, error = _active_organization(request)
            if error:
                return error
            membership, error = _authorize_kind(request=request, organization_id=organization_id, kind=kind)
            if error:
                return error
            uploaded_file = request.FILES.get("file")
            if uploaded_file is None:
                return _problem("spreadsheet_required", "Selecione um arquivo CSV ou .xlsx.", HTTP_400_BAD_REQUEST, fields={"file": ["Este campo é obrigatório."]})
            metadata, errors = _metadata_from_request(request, kind)
            if errors:
                return _problem("validation_error", "Revise os dados informados.", HTTP_400_BAD_REQUEST, fields=errors)
            try:
                rows, file_name, file_sha256 = parse_upload(uploaded_file=uploaded_file, kind=kind)
                preview, _, plan = inspect_batch(
                    kind=kind,
                    rows=rows,
                    metadata=metadata,
                    organization_id=organization_id,
                    actor=request.user,
                )
                scope_error = _authorize_scope(
                    request=request,
                    organization_id=organization_id,
                    membership=membership,
                    kind=kind,
                    metadata=metadata,
                    plan=plan,
                )
                if scope_error:
                    return scope_error
                with transaction.atomic():
                    batch = create_preview(
                        request=request,
                        kind=kind,
                        rows=rows,
                        metadata=metadata,
                        file_name=file_name,
                        file_sha256=file_sha256,
                    )
            except InvalidSpreadsheet as exc:
                return _problem(exc.code, str(exc), HTTP_400_BAD_REQUEST)
            except IntegrityError:
                return _problem("import_preview_conflict", "Os dados mudaram durante a prévia. Envie novamente o arquivo.", HTTP_409_CONFLICT)
        return Response(serialize_batch(batch), status=HTTP_201_CREATED)


@method_decorator(csrf_protect, name="dispatch")
class ImportPreviewDetailView(APIView):
    def get(self, request, batch_id):
        with transaction.atomic():
            organization_id, error = _active_organization(request)
            if error:
                return error
            try:
                batch = ImportBatch.objects.get(
                    organization_id=organization_id,
                    created_by=request.user,
                    pk=batch_id,
                )
            except ImportBatch.DoesNotExist:
                return _problem("import_unavailable", "A prévia não está disponível.", HTTP_404_NOT_FOUND)
            if batch.status == ImportBatch.Status.PREVIEW and batch.expires_at <= timezone.now():
                batch.status = ImportBatch.Status.EXPIRED
                batch.rows = []
                batch.metadata = {}
                batch.save(update_fields=["status", "rows", "metadata"])
        return Response(serialize_batch(batch), status=HTTP_200_OK)

    def post(self, request, batch_id):
        try:
            with transaction.atomic():
                organization_id, error = _active_organization(request)
                if error:
                    return error
                try:
                    batch = ImportBatch.objects.select_for_update().get(
                        organization_id=organization_id,
                        created_by=request.user,
                        pk=batch_id,
                    )
                except ImportBatch.DoesNotExist:
                    return _problem("import_unavailable", "A prévia não está disponível.", HTTP_404_NOT_FOUND)
                membership, error = _authorize_kind(request=request, organization_id=organization_id, kind=batch.kind)
                if error:
                    return error
                _, _, plan = inspect_batch(
                    kind=batch.kind,
                    rows=batch.rows,
                    metadata=batch.metadata,
                    organization_id=organization_id,
                    actor=request.user,
                )
                scope_error = _authorize_scope(
                    request=request,
                    organization_id=organization_id,
                    membership=membership,
                    kind=batch.kind,
                    metadata=batch.metadata,
                    plan=plan,
                )
                if scope_error:
                    return scope_error
                try:
                    confirmed = confirm_batch(request=request, batch_id=batch.pk)
                except ImportNoLongerConfirmable as exc:
                    return _problem(exc.code, str(exc), exc.status, preview=exc.preview)
        except ImportNoLongerConfirmable as exc:
            return _problem(exc.code, str(exc), exc.status, preview=exc.preview)
        except ImportUnavailable:
            return _problem("import_unavailable", "A prévia não está disponível.", HTTP_404_NOT_FOUND)
        except ActiveOrganizationRequired:
            return _problem("active_organization_required", "Selecione uma organização ativa antes de importar dados.", HTTP_404_NOT_FOUND)
        except IntegrityError:
            return _problem("import_preview_stale", "Os dados mudaram durante a confirmação. Gere outra prévia e revise as alterações.", HTTP_409_CONFLICT)
        return Response(serialize_batch(confirmed), status=HTTP_200_OK)
