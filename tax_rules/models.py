# =============================================================================
# tax_rules/models.py
# Modelo inmutable de regla fiscal.
# frozen=True garantiza que ningun agente puede modificar una regla en runtime.
# =============================================================================

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaxRule(BaseModel):
    """
    Representacion inmutable de una regla fiscal para Puerto Rico.

    GARANTIA DE DISENO:
    - frozen=True: ningun atributo puede ser modificado despues de crear la instancia.
    - Solo el modulo admin.py (con autenticacion separada) puede crear reglas nuevas.
    - Los agentes del sistema tienen acceso de LECTURA EXCLUSIVAMENTE.
    - Ninguna funcion de agente acepta un TaxRule como parametro de escritura.
    """

    model_config = ConfigDict(frozen=True)

    # --- Identificacion ---
    rule_id: str = Field(
        description="Identificador unico. Formato: TIPO_JURISDICCION_ANO_VERSION, ej: IVU_ESTATAL_PR_2015_V1"
    )
    name: str = Field(description="Nombre legible de la regla")
    description: str = Field(description="Descripcion completa de la regla y su aplicacion")

    # --- Fuente legal ---
    legal_reference: str = Field(
        description="Texto exacto de referencia legal: ley, articulo, seccion"
    )
    source_url: str = Field(description="URL oficial de la fuente normativa")
    published_date: date = Field(description="Fecha de publicacion oficial del cambio normativo")
    effective_date: date = Field(description="Fecha desde la cual la regla esta en vigor")
    expiry_date: Optional[date] = Field(
        default=None,
        description="Fecha de expiracion. None = vigente indefinidamente hasta supersesion"
    )

    # --- Validacion CPA ---
    validated_by_cpa: bool = Field(
        default=False,
        description="True si un CPA del network Bit-Counting valido esta regla explicitamente"
    )
    validation_date: Optional[date] = Field(
        default=None,
        description="Fecha de la ultima validacion por CPA"
    )
    validating_cpa_license: Optional[str] = Field(
        default=None,
        description="Numero de licencia del CPA que valido la regla"
    )

    # --- Metricas de uso (snapshot, actualizado por admin) ---
    times_applied: int = Field(
        default=0,
        ge=0,
        description="Numero de veces que esta regla ha sido aplicada exitosamente"
    )

    # --- Controversia ---
    has_controversy: bool = Field(
        default=False,
        description="True si existe interpretacion alternativa conocida o disputa activa"
    )
    controversy_notes: Optional[str] = Field(
        default=None,
        description="Descripcion de la controversia o interpretaciones alternativas"
    )

    # --- Versionado ---
    version: int = Field(default=1, ge=1, description="Version de esta regla")
    supersedes_rule_id: Optional[str] = Field(
        default=None,
        description="rule_id de la regla que esta regla reemplaza"
    )

    # --- Tipo y jurisdiccion ---
    tax_type: str = Field(description="Tipo de impuesto o categoria. Ver TaxType enum")
    jurisdiction: str = Field(default="PR", description="Jurisdiccion: PR, FEDERAL, PR_AND_FEDERAL")

    # --- Valores numericos (todos Decimal para precision exacta, nunca float) ---
    rate: Optional[Decimal] = Field(
        default=None,
        description="Tasa como porcentaje. Ej: Decimal('10.5') para 10.5%"
    )
    rate_employee: Optional[Decimal] = Field(
        default=None,
        description="Tasa del empleado (para FICA). Decimal preciso"
    )
    rate_employer: Optional[Decimal] = Field(
        default=None,
        description="Tasa del patrono (para FICA). Decimal preciso"
    )
    wage_base_limit: Optional[Decimal] = Field(
        default=None,
        description="Tope de salario anual al que aplica la tasa. Ej: 176100 para SS 2025"
    )
    additional_rate_threshold: Optional[Decimal] = Field(
        default=None,
        description="Umbral de ingreso a partir del cual aplica tasa adicional"
    )
    additional_rate: Optional[Decimal] = Field(
        default=None,
        description="Tasa adicional sobre el umbral. Ej: Decimal('0.9') para Medicare adicional"
    )
    credit_rate: Optional[Decimal] = Field(
        default=None,
        description="Credito aplicable. Ej: Decimal('5.4') para credito FUTA"
    )
    capitalization_threshold: Optional[Decimal] = Field(
        default=None,
        description="Umbral minimo para capitalizar como activo fijo en vez de gasto"
    )

    # --- Exenciones y condiciones ---
    exemptions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Lista inmutable de categorias exentas de esta regla"
    )
    conditions: tuple[tuple[str, str], ...] = Field(
        default_factory=tuple,
        description="Condiciones adicionales como pares (clave, valor) inmutables"
    )
    deductible_categories: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Categorias deducibles (para reglas DEDUCTIBLE_PR)"
    )

    @model_validator(mode="after")
    def validate_cpa_validation_consistency(self) -> "TaxRule":
        if self.validated_by_cpa and self.validation_date is None:
            raise ValueError(
                f"Regla {self.rule_id}: validated_by_cpa=True requiere validation_date"
            )
        if self.validated_by_cpa and self.validating_cpa_license is None:
            raise ValueError(
                f"Regla {self.rule_id}: validated_by_cpa=True requiere validating_cpa_license"
            )
        if self.expiry_date and self.expiry_date <= self.effective_date:
            raise ValueError(
                f"Regla {self.rule_id}: expiry_date debe ser posterior a effective_date"
            )
        return self

    def is_active_on(self, query_date: date) -> bool:
        """Retorna True si esta regla esta vigente en la fecha indicada."""
        if query_date < self.effective_date:
            return False
        if self.expiry_date is not None and query_date > self.expiry_date:
            return False
        return True

    def net_rate(self) -> Optional[Decimal]:
        """Retorna la tasa neta (rate menos credit_rate si aplica)."""
        if self.rate is None:
            return None
        if self.credit_rate is not None:
            return self.rate - self.credit_rate
        return self.rate

    def __repr__(self) -> str:
        status = "VIGENTE" if self.expiry_date is None else f"EXPIRA {self.expiry_date}"
        return (
            f"TaxRule(id={self.rule_id!r}, "
            f"rate={self.rate}%, "
            f"effective={self.effective_date}, "
            f"status={status})"
        )
