# =============================================================================
# agents/intake.py
# Agente INTAKE — primer agente del flujo de procesamiento.
# =============================================================================

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from .base import BaseAgent
from .exceptions import AgentScopeError, IntakeValidationError
from .messages import IntakeOutput, LineItem, SourceFormat

_CRITICAL_FIELDS = frozenset({"amount", "date"})

_FORMAT_MAP = {
    "ocr_photo": SourceFormat.IMAGE_JPG,
    "ocr_pdf":   SourceFormat.PDF,
    "manual":    SourceFormat.UNKNOWN,
    "csv_import": SourceFormat.CSV,
    "qbo_import": SourceFormat.XML,
    "plaid_api":  SourceFormat.JSON,
    "json":       SourceFormat.JSON,
    "pdf":        SourceFormat.PDF,
}

_BASE_CONFIDENCE = {
    SourceFormat.JSON:      Decimal("0.95"),
    SourceFormat.CSV:       Decimal("0.85"),
    SourceFormat.XML:       Decimal("0.85"),
    SourceFormat.PDF:       Decimal("0.80"),
    SourceFormat.IMAGE_JPG: Decimal("0.70"),
    SourceFormat.IMAGE_PNG: Decimal("0.70"),
    SourceFormat.IMAGE_TIFF: Decimal("0.70"),
    SourceFormat.UNKNOWN:   Decimal("0.65"),
}


class IntakeAgent(BaseAgent):
    """
    Agente INTAKE: extraccion estructurada de documentos financieros.
    NUNCA infiere campos faltantes — los marca y detiene el flujo si son criticos.
    """

    agent_name: str = "INTAKE"
    agent_version: str = "1.0.0"
    allowed_input_types: tuple = ()
    allowed_output_types: tuple = (IntakeOutput,)

    def process_document(
        self,
        raw_input: dict[str, Any],
        source_format: str = "unknown",
        orchestrator=None,
    ) -> IntakeOutput:
        fmt = _FORMAT_MAP.get(source_format.lower(), SourceFormat.UNKNOWN)

        if fmt in (SourceFormat.IMAGE_JPG, SourceFormat.IMAGE_PNG,
                   SourceFormat.IMAGE_TIFF, SourceFormat.UNKNOWN) and "raw_text" in raw_input:
            extracted = self._ocr_extract(raw_input["raw_text"])
            for k, v in raw_input.items():
                if k != "raw_text" and k not in extracted:
                    extracted[k] = v
        else:
            extracted = dict(raw_input)

        vendor         = self._extract_vendor(extracted)
        date_str       = self._extract_date_str(extracted)
        amount         = self._extract_amount(extracted, "amount")
        tax_amount     = self._extract_amount(extracted, "tax_amount")
        currency       = self._extract_currency(extracted)
        line_items     = self._extract_line_items(extracted)
        payment_method = extracted.get("payment_method") or extracted.get("metodo_pago")

        missing: list[str] = []
        if vendor is None:       missing.append("vendor")
        if date_str is None:     missing.append("date")
        if amount is None:       missing.append("amount")
        if tax_amount is None:   missing.append("tax_amount")
        if not line_items:       missing.append("line_items")
        if payment_method is None: missing.append("payment_method")

        has_critical_missing = any(f in _CRITICAL_FIELDS for f in missing)
        confidence = self._calculate_confidence(fmt, missing)

        output = IntakeOutput(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent="INTAKE",
            target_agent="CENTINELA",
            vendor=vendor,
            date=date_str,
            amount=amount,
            tax_amount=tax_amount,
            currency=currency,
            line_items=tuple(
                LineItem(description=i.get("description", ""), amount=Decimal(str(i["amount"])))
                if isinstance(i, dict) and "amount" in i
                else LineItem(description=str(i) if not isinstance(i, dict) else i.get("description",""))
                for i in line_items
            ),
            payment_method=str(payment_method) if payment_method else None,
            confidence=confidence,
            missing_fields=tuple(missing),
            critical_fields_missing=has_critical_missing,
            source_format=fmt,
            raw_text_excerpt=str(raw_input.get("raw_text", ""))[:200] if "raw_text" in raw_input else None,
        )

        if orchestrator is not None:
            orchestrator.record_raw(
                agent_name=self.agent_name,
                input_snapshot={"source_format": source_format, "fields": list(raw_input.keys())},
                output_snapshot={"confidence": str(confidence), "missing": missing},
                rule_ids_applied=[],
                confidence=float(confidence),
            )

        return output

    # ------------------------------------------------------------------
    def _extract_vendor(self, data: dict) -> Optional[str]:
        for key in ("vendor", "vendedor", "proveedor", "merchant", "from", "payee", "nombre"):
            val = data.get(key)
            if val and str(val).strip():
                return str(val).strip()[:255]
        return None

    def _extract_date_str(self, data: dict) -> Optional[str]:
        """Retorna fecha como string ISO 8601 (YYYY-MM-DD) o None."""
        for key in ("date", "fecha", "transaction_date", "fecha_transaccion", "date_posted"):
            val = data.get(key)
            if val is None:
                continue
            if isinstance(val, datetime):
                return val.date().isoformat()
            if isinstance(val, date):
                return val.isoformat()
            parsed = self._parse_date_string(str(val))
            if parsed:
                return parsed.isoformat()
        return None

    def _parse_date_string(self, s: str) -> Optional[date]:
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_amount(self, data: dict, field_name: str) -> Optional[Decimal]:
        aliases = {
            "amount":     ("amount", "monto", "total", "importe", "valor", "precio"),
            "tax_amount": ("tax_amount", "tax", "ivu", "impuesto", "iva", "sales_tax"),
        }
        for key in aliases.get(field_name, (field_name,)):
            val = data.get(key)
            if val is None:
                continue
            try:
                clean = str(val).replace("$", "").replace(",", "").strip()
                if not clean or clean in ("-", "N/A", ""):
                    continue
                return Decimal(clean)
            except InvalidOperation:
                continue
        return None

    def _extract_currency(self, data: dict) -> str:
        for key in ("currency", "moneda", "currency_code"):
            val = data.get(key)
            if val and len(str(val).strip()) == 3:
                return str(val).strip().upper()
        return "USD"

    def _extract_line_items(self, data: dict) -> list[dict]:
        for key in ("line_items", "items", "lineas", "detalle", "details"):
            val = data.get(key)
            if val and isinstance(val, (list, tuple)):
                return [
                    dict(item) if isinstance(item, dict) else {"description": str(item)}
                    for item in val
                ]
        return []

    def _calculate_confidence(self, fmt: SourceFormat, missing: list[str]) -> Decimal:
        score = _BASE_CONFIDENCE.get(fmt, Decimal("0.65"))
        penalties = {"amount": Decimal("0.20"), "date": Decimal("0.20"),
                     "vendor": Decimal("0.05"), "tax_amount": Decimal("0.03"),
                     "line_items": Decimal("0.02")}
        for f in missing:
            score -= penalties.get(f, Decimal("0.01"))
        return max(Decimal("0.0"), min(Decimal("1.0"), score))

    def _ocr_extract(self, raw_text: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        text = raw_text.strip()
        for pattern in (r"Total[:\s]+\$?([\d,]+\.?\d{0,2})",
                         r"Amount[:\s]+\$?([\d,]+\.?\d{0,2})",
                         r"Monto[:\s]+\$?([\d,]+\.?\d{0,2})",
                         r"\$\s*([\d,]+\.\d{2})\b"):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result["amount"] = m.group(1).replace(",", "")
                break
        for pattern in (r"IVU[:\s]+\$?([\d,]+\.?\d{0,2})",
                         r"Tax[:\s]+\$?([\d,]+\.?\d{0,2})"):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result["tax_amount"] = m.group(1).replace(",", "")
                break
        for pattern in (r"\b(\d{1,2}/\d{1,2}/\d{4})\b", r"\b(\d{4}-\d{2}-\d{2})\b"):
            m = re.search(pattern, text)
            if m:
                result["date"] = m.group(1)
                break
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if lines:
            first = lines[0]
            if len(first) < 100 and not re.match(r"^\$|^\d", first):
                result["vendor"] = first
        result["raw_text"] = raw_text
        return result

    def _process_impl(self, message):
        raise AgentScopeError(
            agent_name=self.agent_name,
            message_type=type(message).__name__,
            allowed_types=(),
        )
