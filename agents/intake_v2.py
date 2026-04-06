# =============================================================================
# agents/intake_v2.py
# Agente INTAKE V2 — extraccion estructurada via GPT-4o Vision API.
#
# GARANTIAS DE DISENO:
#   - La extraccion OCR/estructural se delega SIEMPRE a GPT-4o Vision.
#   - Campos no detectables se marcan MISSING — NUNCA se infieren.
#   - Si campos criticos (amount, date) estan MISSING: detiene el flujo
#     y emite MissingCriticalFieldsError con lista especifica de campos.
#   - Toda decision queda registrada en AgentDecisionsLog (append-only).
#   - _vision_client es inyectable para facilitar tests sin API real.
#   - IntakeResultV2 es frozen=True — inmutable una vez creado.
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .core_decisions import get_decisions_log, AgentDecisionsLog
from .exceptions import BitCountingAgentError

logger = logging.getLogger(__name__)

_CRITICAL_FIELDS = frozenset({"amount", "date"})


# ---------------------------------------------------------------------------
# Excepciones especificas
# ---------------------------------------------------------------------------

class MissingCriticalFieldsError(BitCountingAgentError):
    """
    Campos criticos faltantes en el documento — el flujo no puede continuar.

    El CPA debe proveer los datos faltantes especificos listados en
    missing_critical antes de que INTAKE pueda reprocessar el documento.
    """
    def __init__(self, missing_critical: list[str], source_format: str) -> None:
        self.missing_critical = missing_critical
        self.source_format = source_format
        super().__init__(
            f"INTAKE V2: campos criticos faltantes {missing_critical} en "
            f"documento '{source_format}'. Solicitar al usuario: "
            + ", ".join(missing_critical)
        )


# ---------------------------------------------------------------------------
# Modelos de resultado
# ---------------------------------------------------------------------------

class DocumentType(str, Enum):
    INVOICE      = "INVOICE"
    RECEIPT      = "RECEIPT"
    PAYROLL      = "PAYROLL"
    BANK_STMT    = "BANK_STMT"
    TAX_FORM     = "TAX_FORM"
    CREDIT_NOTE  = "CREDIT_NOTE"
    UNKNOWN      = "UNKNOWN"


class LineItemV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    description:  str
    quantity:     Optional[Decimal] = None
    unit_price:   Optional[Decimal] = None
    amount:       Decimal
    tax_amount:   Optional[Decimal] = None
    account_hint: Optional[str] = None


class IntakeResultV2(BaseModel):
    """
    Output de INTAKE V2. Frozen — inmutable una vez creado.

    missing_fields lista todos los campos no detectados.
    critical_fields_missing=True indica que el flujo debe detenerse.
    """
    model_config = ConfigDict(frozen=True)

    result_id:               str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_format:           str
    document_type:           DocumentType = DocumentType.UNKNOWN

    # Campos extraidos
    vendor:          Optional[str]     = None
    date:            Optional[str]     = None   # ISO 8601
    amount:          Optional[Decimal] = None
    tax_amount:      Optional[Decimal] = None
    currency:        str               = "USD"
    payment_method:  Optional[str]     = None
    line_items:      tuple[LineItemV2, ...] = ()

    # Metadatos de calidad
    confidence:              Decimal
    missing_fields:          tuple[str, ...] = ()
    critical_fields_missing: bool = False
    raw_extract:             str = ""   # fragmento del JSON retornado por Vision API
    extracted_at:            str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decision_id:             Optional[str] = None  # AgentDecision.decision_id


# ---------------------------------------------------------------------------
# Protocolo del cliente Vision (inyectable para tests)
# ---------------------------------------------------------------------------

class VisionClientProtocol(Protocol):
    """
    Interfaz que debe implementar cualquier cliente Vision.
    El cliente real usa OpenAI GPT-4o; los tests inyectan un mock.
    """
    def extract(
        self,
        image_bytes: bytes,
        source_format: str,
    ) -> dict[str, Any]:
        """
        Llama a GPT-4o Vision y retorna un dict con los campos extraidos.
        Nunca lanza — retorna {"error": "..."} si falla.
        """
        ...


class _OpenAIVisionClient:
    """
    Implementacion real usando GPT-4o Vision API.
    Requiere que OPENAI_API_KEY este en el entorno.
    """

    _SYSTEM_PROMPT = (
        "Eres un extractor de documentos contables. "
        "Extrae exactamente estos campos del documento: "
        "vendor (string), date (ISO 8601 YYYY-MM-DD), amount (decimal string), "
        "tax_amount (decimal string o null), currency (ISO 4217, default USD), "
        "payment_method (string o null), document_type "
        "(INVOICE|RECEIPT|PAYROLL|BANK_STMT|TAX_FORM|CREDIT_NOTE|UNKNOWN), "
        "line_items (array de {description, quantity, unit_price, amount, tax_amount}). "
        "Si un campo no es detectable con certeza, usa null — NUNCA inferas. "
        "Retorna SOLO JSON valido, sin markdown."
    )

    def __init__(self) -> None:
        try:
            import openai
            self._client = openai.OpenAI()
        except ImportError:
            self._client = None
            logger.warning("openai package no disponible — INTAKE V2 en modo degradado")

    def extract(
        self,
        image_bytes: bytes,
        source_format: str,
    ) -> dict[str, Any]:
        if self._client is None:
            return {"error": "openai package not installed"}

        # Codificar imagen como base64 para la API de Vision
        b64 = base64.b64encode(image_bytes).decode()
        mime = _format_to_mime(source_format)

        try:
            response = self._client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{b64}",
                                    "detail": "high",
                                },
                            },
                            {
                                "type": "text",
                                "text": "Extrae los campos contables de este documento.",
                            },
                        ],
                    },
                ],
                max_tokens=1000,
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except Exception as exc:
            logger.error("GPT-4o Vision error: %s", exc)
            return {"error": str(exc)}


def _format_to_mime(source_format: str) -> str:
    return {
        "pdf":        "application/pdf",
        "jpg":        "image/jpeg",
        "jpeg":       "image/jpeg",
        "png":        "image/png",
        "tiff":       "image/tiff",
        "tif":        "image/tiff",
    }.get(source_format.lower(), "image/jpeg")


# ---------------------------------------------------------------------------
# Agente INTAKE V2
# ---------------------------------------------------------------------------

class IntakeAgentV2:
    """
    Agente INTAKE V2 — extraccion estructurada de documentos via GPT-4o Vision.

    Uso:
        agent = IntakeAgentV2()
        result = agent.process_document(image_bytes, source_format="jpg")

    Para tests:
        agent = IntakeAgentV2(vision_client=MockVisionClient())
    """

    AGENT_NAME = "INTAKE_V2"

    def __init__(
        self,
        vision_client: Optional[VisionClientProtocol] = None,
        decisions_log: Optional[AgentDecisionsLog] = None,
    ) -> None:
        self._vision = vision_client or _OpenAIVisionClient()
        self._log    = decisions_log if decisions_log is not None else get_decisions_log()

    # ------------------------------------------------------------------ #
    # Punto de entrada principal                                           #
    # ------------------------------------------------------------------ #

    def process_document(
        self,
        document_bytes: bytes,
        source_format:  str,
        raise_on_missing_critical: bool = True,
    ) -> IntakeResultV2:
        """
        Procesa un documento y retorna IntakeResultV2.

        Args:
            document_bytes:              Contenido binario del documento.
            source_format:               "pdf", "jpg", "png", "tiff", etc.
            raise_on_missing_critical:   Si True (default) lanza
                                         MissingCriticalFieldsError cuando
                                         faltan campos criticos (amount/date).
                                         Si False retorna el resultado con
                                         critical_fields_missing=True.

        Returns:
            IntakeResultV2 con todos los campos y su estado de deteccion.

        Raises:
            MissingCriticalFieldsError: cuando campos criticos son MISSING
                                        y raise_on_missing_critical=True.
        """
        # Llamar Vision API
        raw = self._vision.extract(document_bytes, source_format)

        if "error" in raw:
            logger.error("INTAKE V2: Vision API error: %s", raw["error"])
            # Retornar resultado de error sin bloquear el sistema
            result = IntakeResultV2(
                source_format=source_format,
                confidence=Decimal("0.0"),
                missing_fields=("vendor", "date", "amount", "tax_amount",
                                "payment_method", "line_items"),
                critical_fields_missing=True,
                raw_extract=json.dumps(raw),
            )
            self._log.record(
                agent_name=self.AGENT_NAME,
                input_data={"source_format": source_format, "bytes_len": len(document_bytes)},
                decision=f"VISION_API_ERROR: {raw['error']}",
                notes="Retornando resultado vacio — Vision API no disponible",
            )
            if raise_on_missing_critical:
                raise MissingCriticalFieldsError(["amount", "date"], source_format)
            return result

        # Parsear campos
        vendor        = _str_field(raw, "vendor")
        date_str      = _date_field(raw, "date")
        amount        = _decimal_field(raw, "amount")
        tax_amount    = _decimal_field(raw, "tax_amount")
        currency      = _currency_field(raw)
        payment_meth  = _str_field(raw, "payment_method")
        doc_type      = _doc_type_field(raw)
        line_items    = _line_items_field(raw)

        # Detectar campos faltantes
        missing: list[str] = []
        if vendor is None:       missing.append("vendor")
        if date_str is None:     missing.append("date")
        if amount is None:       missing.append("amount")
        if tax_amount is None:   missing.append("tax_amount")
        if payment_meth is None: missing.append("payment_method")
        if not line_items:       missing.append("line_items")

        critical_missing = [f for f in missing if f in _CRITICAL_FIELDS]
        confidence       = _compute_confidence(source_format, missing)

        # Registrar decision
        decision_entry = self._log.record(
            agent_name=self.AGENT_NAME,
            input_data={"source_format": source_format, "bytes_len": len(document_bytes)},
            decision=(
                f"Extraccion completada. Confianza: {confidence}. "
                f"Campos faltantes: {missing or 'ninguno'}. "
                f"Criticos faltantes: {critical_missing or 'ninguno'}."
            ),
            confidence=float(confidence),
            notes=f"doc_type={doc_type.value}",
        )

        result = IntakeResultV2(
            source_format=source_format,
            document_type=doc_type,
            vendor=vendor,
            date=date_str,
            amount=amount,
            tax_amount=tax_amount,
            currency=currency,
            payment_method=payment_meth,
            line_items=tuple(line_items),
            confidence=confidence,
            missing_fields=tuple(missing),
            critical_fields_missing=bool(critical_missing),
            raw_extract=json.dumps(raw)[:500],
            decision_id=decision_entry.decision_id,
        )

        if critical_missing and raise_on_missing_critical:
            raise MissingCriticalFieldsError(critical_missing, source_format)

        return result


# ---------------------------------------------------------------------------
# Helpers de parseo
# ---------------------------------------------------------------------------

def _str_field(data: dict, key: str) -> Optional[str]:
    val = data.get(key)
    if val is None or str(val).strip().lower() in ("null", "none", ""):
        return None
    return str(val).strip()[:255]


def _date_field(data: dict, key: str) -> Optional[str]:
    val = data.get(key)
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("null", "none", ""):
        return None
    # Aceptar ISO 8601 YYYY-MM-DD o MM/DD/YYYY
    from datetime import date as _date
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _decimal_field(data: dict, key: str) -> Optional[Decimal]:
    val = data.get(key)
    if val is None:
        return None
    s = str(val).replace("$", "").replace(",", "").strip()
    if s.lower() in ("null", "none", ""):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _currency_field(data: dict) -> str:
    val = data.get("currency")
    if val and len(str(val).strip()) == 3:
        return str(val).strip().upper()
    return "USD"


def _doc_type_field(data: dict) -> DocumentType:
    val = data.get("document_type", "")
    try:
        return DocumentType(str(val).upper())
    except ValueError:
        return DocumentType.UNKNOWN


def _line_items_field(data: dict) -> list[LineItemV2]:
    raw_items = data.get("line_items") or []
    if not isinstance(raw_items, list):
        return []
    result = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            amt = _decimal_field(item, "amount")
            if amt is None:
                continue
            result.append(LineItemV2(
                description=str(item.get("description", ""))[:255],
                quantity=_decimal_field(item, "quantity"),
                unit_price=_decimal_field(item, "unit_price"),
                amount=amt,
                tax_amount=_decimal_field(item, "tax_amount"),
                account_hint=_str_field(item, "account_hint"),
            ))
        except Exception:
            continue
    return result


_BASE_CONFIDENCE = {
    "json":  Decimal("0.95"),
    "csv":   Decimal("0.85"),
    "xml":   Decimal("0.85"),
    "pdf":   Decimal("0.80"),
    "jpg":   Decimal("0.75"),
    "jpeg":  Decimal("0.75"),
    "png":   Decimal("0.75"),
    "tiff":  Decimal("0.70"),
    "tif":   Decimal("0.70"),
}

_FIELD_PENALTIES = {
    "amount":         Decimal("0.20"),
    "date":           Decimal("0.20"),
    "vendor":         Decimal("0.05"),
    "tax_amount":     Decimal("0.03"),
    "payment_method": Decimal("0.02"),
    "line_items":     Decimal("0.02"),
}


def _compute_confidence(source_format: str, missing: list[str]) -> Decimal:
    base = _BASE_CONFIDENCE.get(source_format.lower(), Decimal("0.65"))
    for field in missing:
        base -= _FIELD_PENALTIES.get(field, Decimal("0.01"))
    return max(Decimal("0.0"), min(Decimal("1.0"), base))
