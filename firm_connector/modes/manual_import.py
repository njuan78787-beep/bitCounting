# =============================================================================
# firm_connector/modes/manual_import.py
# Mode 3 — Manual file import (CSV / Excel / JSON / XML).
#
# DESIGN PRINCIPLES:
#   - All-or-nothing: the entire file is validated before any record is stored.
#     If any row fails schema validation, the whole import is rejected.
#   - SSN arrives as plaintext; encrypt_field() is called immediately before
#     any other processing; plaintext is discarded.
#   - Row errors include row number + field name but NEVER the field value.
#   - Supports: CSV, XLSX/XLS (openpyxl), JSON, XML.
# =============================================================================

from __future__ import annotations

import csv
import io
import json
import logging
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from ..models import (
    EmployeeImportRow,
    ImportFormat,
    RecordError,
    TransactionFirm,
    ClientFirm,
    PayType,
    TransactionType,
    BusinessType,
    AccountingMethod,
)

logger = logging.getLogger(__name__)


class ImportValidationError(Exception):
    """Raised when one or more rows fail validation (all-or-nothing)."""

    def __init__(self, errors: List[RecordError]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s) in imported file")


class ManualImportMode:
    """
    Parses and validates firm data from uploaded files.

    Usage:
        mode = ManualImportMode()
        employees = mode.parse_employees(file_bytes, ImportFormat.CSV)
        # Returns List[EmployeeImportRow] — caller must call .to_employee_firm() immediately.
    """

    # -----------------------------------------------------------------------
    # Public parse entry points
    # -----------------------------------------------------------------------

    def parse_employees(
        self, file_bytes: bytes, fmt: ImportFormat
    ) -> List[EmployeeImportRow]:
        """
        Parse employee records from raw file bytes.

        All-or-nothing: raises ImportValidationError if ANY row is invalid.
        SSN validation is performed here (9 digits required); encryption
        happens in the caller (sync.py) via EmployeeImportRow.to_employee_firm().

        Returns a list of EmployeeImportRow (plaintext SSN transiently present).
        """
        rows = self._load_rows(file_bytes, fmt)
        records: List[EmployeeImportRow] = []
        errors: List[RecordError] = []

        for idx, row in enumerate(rows, start=2):   # row 1 = header
            row_id = row.get("employee_id", f"row_{idx}")
            try:
                record = EmployeeImportRow(
                    employee_id=_require(row, "employee_id", row_id),
                    firm_client_id=_require(row, "firm_client_id", row_id),
                    name=_require(row, "name", row_id),
                    ssn=_require(row, "ssn", row_id),
                    hire_date=_parse_date(row, "hire_date", row_id),
                    termination_date=_parse_date_optional(row, "termination_date"),
                    pay_type=PayType(_require(row, "pay_type", row_id).upper()),
                    pay_rate=_parse_decimal(row, "pay_rate", row_id),
                    filing_status_federal=_require(row, "filing_status_federal", row_id),
                    allowances_federal=int(row.get("allowances_federal", 0) or 0),
                    filing_status_pr=_require(row, "filing_status_pr", row_id),
                    allowances_pr=int(row.get("allowances_pr", 0) or 0),
                    ytd_gross=_parse_decimal_opt(row, "ytd_gross"),
                    ytd_ss=_parse_decimal_opt(row, "ytd_ss"),
                    ytd_medicare=_parse_decimal_opt(row, "ytd_medicare"),
                    ytd_federal_tax=_parse_decimal_opt(row, "ytd_federal_tax"),
                    ytd_pr_tax=_parse_decimal_opt(row, "ytd_pr_tax"),
                    ytd_futa=_parse_decimal_opt(row, "ytd_futa"),
                    ytd_suta=_parse_decimal_opt(row, "ytd_suta"),
                )
                records.append(record)
            except (ValueError, KeyError, Exception) as exc:
                # Never include field values in the error
                field = _extract_field_from_exc(exc)
                errors.append(RecordError(
                    record_id=str(row_id),
                    reason=_sanitize_error(str(exc)),
                    field=field,
                ))

        if errors:
            logger.warning(
                "IMPORT: Employee file rejected — %d row error(s) (all-or-nothing)",
                len(errors),
            )
            raise ImportValidationError(errors)

        logger.info("IMPORT: Parsed %d employee records (SSN plaintext — encrypt immediately)", len(records))
        return records

    def parse_transactions(
        self, file_bytes: bytes, fmt: ImportFormat
    ) -> List[TransactionFirm]:
        """
        Parse transaction records. Non-sensitive — no encryption needed.
        All-or-nothing: raises ImportValidationError if ANY row is invalid.
        """
        rows = self._load_rows(file_bytes, fmt)
        records: List[TransactionFirm] = []
        errors: List[RecordError] = []

        for idx, row in enumerate(rows, start=2):
            row_id = row.get("transaction_id", f"row_{idx}")
            try:
                record = TransactionFirm(
                    transaction_id=_require(row, "transaction_id", row_id),
                    firm_client_id=_require(row, "firm_client_id", row_id),
                    date=_require(row, "date", row_id),
                    description=_require(row, "description", row_id),
                    amount=_parse_decimal(row, "amount", row_id),
                    type=TransactionType(_require(row, "type", row_id).upper()),
                    account_code=_require(row, "account_code", row_id),
                    vendor_or_client=row.get("vendor_or_client") or None,
                    invoice_number=row.get("invoice_number") or None,
                    ivu_collected=_parse_decimal_opt(row, "ivu_collected"),
                    ivu_paid=_parse_decimal_opt(row, "ivu_paid"),
                    is_payroll=_parse_bool(row.get("is_payroll", False)),
                    document_url=row.get("document_url") or None,
                    already_processed=_parse_bool(row.get("already_processed", False)),
                )
                records.append(record)
            except (ValueError, KeyError, Exception) as exc:
                field = _extract_field_from_exc(exc)
                errors.append(RecordError(
                    record_id=str(row_id),
                    reason=_sanitize_error(str(exc)),
                    field=field,
                ))

        if errors:
            logger.warning(
                "IMPORT: Transaction file rejected — %d row error(s) (all-or-nothing)",
                len(errors),
            )
            raise ImportValidationError(errors)

        logger.info("IMPORT: Parsed %d transaction records", len(records))
        return records

    def parse_clients(
        self, file_bytes: bytes, fmt: ImportFormat
    ) -> List[Dict[str, Any]]:
        """
        Parse client records from file. Returns raw dicts for sync.py to
        validate through ClientFirm model (which requires ssn_or_ein_federal
        to already be encrypted by the caller).

        Returns raw row dicts — caller is responsible for encryption and
        ClientFirm model construction.
        """
        rows = self._load_rows(file_bytes, fmt)
        logger.info("IMPORT: Loaded %d client rows for further processing", len(rows))
        return rows

    # -----------------------------------------------------------------------
    # Format dispatch
    # -----------------------------------------------------------------------

    def _load_rows(self, file_bytes: bytes, fmt: ImportFormat) -> List[Dict[str, Any]]:
        if fmt == ImportFormat.CSV:
            return _parse_csv(file_bytes)
        elif fmt == ImportFormat.EXCEL:
            return _parse_excel(file_bytes)
        elif fmt == ImportFormat.JSON:
            return _parse_json(file_bytes)
        elif fmt == ImportFormat.XML:
            return _parse_xml(file_bytes)
        else:
            raise ValueError(f"Unsupported import format: {fmt}")


# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------

def _parse_csv(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse UTF-8 CSV (with BOM tolerance)."""
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(row) for row in reader]
    if not rows:
        raise ImportValidationError([RecordError(record_id="file", reason="CSV file contains no data rows")])
    return rows


def _parse_excel(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse .xlsx/.xls using openpyxl."""
    try:
        import openpyxl  # type: ignore[import]
    except ImportError:
        raise RuntimeError("openpyxl is required for Excel import: pip install openpyxl")

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    header = [str(cell) if cell is not None else "" for cell in next(rows_iter, [])]
    if not header:
        raise ImportValidationError([RecordError(record_id="file", reason="Excel file has no header row")])

    result: List[Dict[str, Any]] = []
    for row_values in rows_iter:
        if all(v is None for v in row_values):
            continue   # skip blank rows
        result.append({header[i]: (str(v) if v is not None else "") for i, v in enumerate(row_values)})

    wb.close()
    if not result:
        raise ImportValidationError([RecordError(record_id="file", reason="Excel file contains no data rows")])
    return result


def _parse_json(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Parse JSON array or object with an 'items'/'data' wrapper."""
    try:
        payload = json.loads(file_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ImportValidationError([RecordError(record_id="file", reason=f"Invalid JSON: {exc.msg}")])

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("items") or payload.get("data") or payload.get("records") or []
    else:
        raise ImportValidationError([RecordError(record_id="file", reason="JSON root must be an array or object with 'items'/'data' key")])

    if not rows:
        raise ImportValidationError([RecordError(record_id="file", reason="JSON file contains no records")])

    # Normalize all values to strings for consistent downstream parsing
    return [{k: (str(v) if v is not None else "") for k, v in row.items()} for row in rows]


def _parse_xml(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse XML of the form:
      <records>
        <record>
          <field_name>value</field_name>
          ...
        </record>
      </records>
    Root element name and record element name are flexible.
    """
    try:
        root = ET.fromstring(file_bytes)
    except ET.ParseError as exc:
        raise ImportValidationError([RecordError(record_id="file", reason=f"Invalid XML: {exc}")])

    # Find record elements (children of root)
    record_elements = list(root)
    if not record_elements:
        raise ImportValidationError([RecordError(record_id="file", reason="XML file contains no record elements")])

    rows: List[Dict[str, Any]] = []
    for elem in record_elements:
        row: Dict[str, Any] = {}
        for child in elem:
            row[child.tag] = (child.text or "").strip()
        if row:
            rows.append(row)

    if not rows:
        raise ImportValidationError([RecordError(record_id="file", reason="XML file produced no parseable rows")])
    return rows


# ---------------------------------------------------------------------------
# Field helpers (never include field values in error messages)
# ---------------------------------------------------------------------------

def _require(row: Dict[str, Any], field: str, row_id: Any) -> str:
    val = row.get(field)
    if val is None or str(val).strip() == "":
        raise ValueError(f"Missing required field '{field}' on record {row_id}")
    return str(val).strip()


def _parse_date(row: Dict[str, Any], field: str, row_id: Any):
    from datetime import date
    raw = _require(row, field, row_id)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"Field '{field}' has invalid date format on record {row_id} (expected YYYY-MM-DD)")


def _parse_date_optional(row: Dict[str, Any], field: str):
    from datetime import date
    raw = row.get(field)
    if not raw or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def _parse_decimal(row: Dict[str, Any], field: str, row_id: Any) -> Decimal:
    raw = _require(row, field, row_id)
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"Field '{field}' is not a valid decimal on record {row_id}")


def _parse_decimal_opt(row: Dict[str, Any], field: str) -> Decimal:
    raw = row.get(field)
    if not raw or str(raw).strip() == "":
        return Decimal("0.00")
    try:
        return Decimal(str(raw).replace(",", ""))
    except InvalidOperation:
        return Decimal("0.00")


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y")


def _sanitize_error(msg: str) -> str:
    """Strip anything that looks like a field value from error messages."""
    # Truncate very long messages that might contain values
    if len(msg) > 200:
        msg = msg[:200] + "…"
    return msg


def _extract_field_from_exc(exc: Exception) -> Optional[str]:
    """Attempt to extract the field name from a Pydantic or ValueError message."""
    msg = str(exc)
    # Pydantic v2 errors often contain the field name
    for keyword in ("field '", "Field '", "column '"):
        idx = msg.find(keyword)
        if idx != -1:
            start = idx + len(keyword)
            end = msg.find("'", start)
            if end != -1:
                return msg[start:end]
    return None
