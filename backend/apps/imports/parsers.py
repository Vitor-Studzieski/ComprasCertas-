"""Strict CSV/XLSX parsing for the documented Brazilian import templates."""

import csv
import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from apps.imports.models import ImportBatch


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_XLSX_BYTES = 100 * 1024 * 1024
MAX_ROWS = 10_000

HEADERS = {
    ImportBatch.Kind.PRODUCTS: ("SKU", "Nome", "Unidade", "Categoria"),
    ImportBatch.Kind.SUPPLIERS: ("Nome", "Contato", "E-mail", "Telefone", "Observações"),
    ImportBatch.Kind.CONDITIONS: (
        "SKU", "Fornecedor", "Loja", "Vigente desde", "Unidade de compra",
        "Unidades por embalagem", "Preço por embalagem", "Prazo (dias corridos)",
        "Pedido mínimo", "Múltiplo de compra",
    ),
    ImportBatch.Kind.OPENING_STOCK: ("SKU", "Quantidade"),
    ImportBatch.Kind.DAILY_SALES: ("SKU", "Data", "Quantidade"),
    ImportBatch.Kind.OUT_OF_STOCK_DAYS: ("SKU", "Data"),
}


class InvalidSpreadsheet(Exception):
    def __init__(self, message, code="invalid_spreadsheet"):
        super().__init__(message)
        self.code = code


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, Decimal):
        return format(value, "f").replace(".", ",")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f").replace(".", ",")
    if isinstance(value, bool):
        raise InvalidSpreadsheet("Valores booleanos não são aceitos nas planilhas.")
    return str(value).strip()


def _normalize_row(kind, values, row_number):
    headers = HEADERS[kind]
    row = {key: _cell_text(value) for key, value in zip(headers, values)}
    row["rowNumber"] = row_number

    if kind == ImportBatch.Kind.PRODUCTS:
        row["sku"] = row.pop("SKU").strip()
        row["name"] = row.pop("Nome").strip()
        raw_unit = row.pop("Unidade").strip().lower()
        row["unit"] = {"unidade": "unit", "un": "unit", "unit": "unit", "kg": "kg", "litro": "l", "l": "l"}.get(raw_unit, raw_unit)
        row["category"] = row.pop("Categoria").strip()
    elif kind == ImportBatch.Kind.SUPPLIERS:
        row["name"] = row.pop("Nome").strip()
        row["contactName"] = row.pop("Contato").strip()
        row["email"] = row.pop("E-mail").strip()
        row["phone"] = row.pop("Telefone").strip()
        row["notes"] = row.pop("Observações").strip()
    elif kind == ImportBatch.Kind.CONDITIONS:
        row["sku"] = row.pop("SKU").strip()
        row["supplier"] = row.pop("Fornecedor").strip()
        row["store"] = row.pop("Loja").strip()
        row["effectiveFrom"] = row.pop("Vigente desde").strip()
        row["purchaseUnitName"] = row.pop("Unidade de compra").strip()
        row["baseUnitsPerPurchaseUnit"] = row.pop("Unidades por embalagem").strip()
        row["pricePerPurchaseUnit"] = row.pop("Preço por embalagem").strip()
        row["deliveryLeadTimeDays"] = row.pop("Prazo (dias corridos)").strip()
        row["minimumPurchaseQuantity"] = row.pop("Pedido mínimo").strip()
        row["purchaseMultiple"] = row.pop("Múltiplo de compra").strip()
    elif kind == ImportBatch.Kind.OPENING_STOCK:
        row["sku"] = row.pop("SKU").strip()
        row["quantity"] = row.pop("Quantidade").strip()
    elif kind == ImportBatch.Kind.DAILY_SALES:
        row["sku"] = row.pop("SKU").strip()
        row["operationalDate"] = row.pop("Data").strip()
        row["quantity"] = row.pop("Quantidade").strip()
    else:
        row["sku"] = row.pop("SKU").strip()
        row["operationalDate"] = row.pop("Data").strip()
    return row


def _read_csv(contents):
    try:
        text = contents.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidSpreadsheet("O CSV precisa usar codificação UTF-8.") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
    try:
        return [row for row in reader]
    except csv.Error as exc:
        raise InvalidSpreadsheet("O CSV contém aspas ou separadores inválidos.") from exc


def _read_xlsx(contents):
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_UNCOMPRESSED_XLSX_BYTES:
                raise InvalidSpreadsheet("A planilha Excel excede o limite de conteúdo descompactado.")
            if len(archive.infolist()) > 2_000:
                raise InvalidSpreadsheet("A planilha Excel contém estrutura excessivamente grande.")
        workbook = load_workbook(io.BytesIO(contents), read_only=True, data_only=False, keep_links=False)
    except InvalidSpreadsheet:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise InvalidSpreadsheet("Não foi possível abrir o arquivo Excel .xlsx.") from exc

    try:
        worksheet = workbook.active
        rows = []
        for source_row in worksheet.iter_rows():
            values = []
            for cell in source_row:
                if cell.data_type == "f":
                    raise InvalidSpreadsheet(f"Fórmulas não são aceitas (linha {cell.row}).")
                values.append(cell.value)
            rows.append(values)
            if len(rows) > MAX_ROWS + 1:
                raise InvalidSpreadsheet(f"O arquivo excede o limite de {MAX_ROWS} linhas de dados.")
        return rows
    finally:
        workbook.close()


def _validate_header(raw_header, expected):
    header = [_cell_text(value) for value in raw_header]
    while header and header[-1] == "":
        header.pop()
    if tuple(header) != tuple(expected):
        raise InvalidSpreadsheet(
            "Os cabeçalhos precisam corresponder exatamente ao modelo CompraCerta: "
            + ";".join(expected),
            code="spreadsheet_headers_invalid",
        )


def parse_upload(*, uploaded_file, kind):
    if kind not in HEADERS:
        raise InvalidSpreadsheet("Tipo de importação não reconhecido.", code="import_kind_invalid")
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise InvalidSpreadsheet("O arquivo ultrapassa o limite de 10 MB.", code="spreadsheet_too_large")

    file_name = str(uploaded_file.name).replace("\\", "/").split("/")[-1][:255]
    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension not in {"csv", "xlsx"}:
        raise InvalidSpreadsheet("Envie um arquivo CSV ou Excel .xlsx.", code="spreadsheet_format_invalid")

    contents = uploaded_file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise InvalidSpreadsheet("O arquivo ultrapassa o limite de 10 MB.", code="spreadsheet_too_large")
    try:
        raw_rows = _read_csv(contents) if extension == "csv" else _read_xlsx(contents)
    except (csv.Error, UnicodeError, InvalidSpreadsheet):
        raise

    if not raw_rows:
        raise InvalidSpreadsheet("A planilha está vazia.")
    _validate_header(raw_rows[0], HEADERS[kind])

    normalized = []
    for index, values in enumerate(raw_rows[1:], start=2):
        if not any(_cell_text(value) for value in values):
            continue
        if len(values) > len(HEADERS[kind]) and any(_cell_text(value) for value in values[len(HEADERS[kind]):]):
            raise InvalidSpreadsheet(f"A linha {index} contém colunas adicionais.", code="spreadsheet_columns_invalid")
        if len(values) < len(HEADERS[kind]):
            values = [*values, *([None] * (len(HEADERS[kind]) - len(values)))]
        normalized.append(_normalize_row(kind, values, index))
        if len(normalized) > MAX_ROWS:
            raise InvalidSpreadsheet(f"O arquivo excede o limite de {MAX_ROWS} linhas de dados.", code="spreadsheet_too_many_rows")

    if not normalized:
        raise InvalidSpreadsheet("Inclua ao menos uma linha de dados.")
    return normalized, file_name, hashlib.sha256(contents).hexdigest()


def parse_decimal(value, *, decimal_places, allow_zero=True, max_digits=14):
    text = str(value).strip()
    if not text or text.startswith("-") or text.startswith("+"):
        raise ValueError
    if "." in text:
        raise ValueError
    normalized = text.replace(",", ".")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", normalized):
        raise ValueError
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError from exc
    integer_digits = max(number.adjusted() + 1, 0) if number else 0
    if (
        not number.is_finite()
        or number.as_tuple().exponent < -decimal_places
        or integer_digits > max_digits - decimal_places
        or len(number.as_tuple().digits) > max_digits
    ):
        raise ValueError
    if not allow_zero and number <= 0:
        raise ValueError
    return format(number.normalize(), "f") if number else "0"


def parse_operational_date(value):
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValueError from exc
