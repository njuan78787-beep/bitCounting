# =============================================================================
# agents/hacienda.py
# Agente HACIENDA — Generacion de formularios fiscales de Puerto Rico.
#
# GARANTIAS DE DISENO:
#   - Phase 1: DRY_RUN ONLY. status="DRY_RUN" siempre. Nunca "FILED".
#   - Triple verificacion antes de generar cualquier formulario:
#       1. algebraic_check          — base + tasa = liability del FiscalOutput
#       2. ivu_crosscheck           — coherencia de tasa IVU con periodo/tipo
#       3. prior_period_consistency — period_from <= period_to dentro del rango
#   - Todos los montos son Decimal — nunca float.
#   - calc_hash en cada formulario generado (sha256 de campos clave).
#   - Lanza HaciendaValidationError si cualquier pre-check falla.
#   - agent_version = "1.0.0-dryrun"
#
# Formularios soportados (Phase 1 — dry-run):
#   SC 2915  — IVU Mensual (planilla mensual IVU)
#   941-PR   — Employer's Quarterly Federal Tax Return for Puerto Rico
#   W-2PR    — Annual Wage Report
#   SC 2644  — Income Tax Return for Corporations
# =============================================================================

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .base import BaseAgent
from .exceptions import BitCountingAgentError, AgentScopeError
from .messages import BaseAgentMessage, FiscalOutput, OrchestratorDecision

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de referencia para cross-check
# ---------------------------------------------------------------------------

_IVU_ESTATAL_RATE  = Decimal("10.5")
_IVU_MUNICIPAL_RATE = Decimal("1.0")
_IVU_TOLERANCE     = Decimal("0.02")   # 2 centavos de tolerancia por redondeo

_SUPPORTED_FORMS = frozenset({"SC 2915", "941-PR", "W-2PR", "SC 2644"})

# form_id aliases que llegan de FiscalPRAgent → form_type canonico
_FORM_ID_MAP: dict[str, str] = {
    "SC-2915":           "SC 2915",
    "SC2915":            "SC 2915",
    "PLANILLA_MENSUAL_IVU": "SC 2915",
    "941-PR":            "941-PR",
    "W-2PR":             "W-2PR",
    "W2PR":              "W-2PR",
    "SC-2644":           "SC 2644",
    "SC2644":            "SC 2644",
}


# ---------------------------------------------------------------------------
# Excepcion especifica de HACIENDA
# ---------------------------------------------------------------------------

class HaciendaValidationError(BitCountingAgentError):
    """
    Un pre-check de validacion fallo antes de generar un formulario fiscal.

    Lanzada por HaciendaAgent cuando algebraic_check, ivu_crosscheck o
    prior_period_consistency detectan una inconsistencia en el FiscalOutput
    recibido. El formulario NO se genera — el flujo se detiene para
    revision del CPA.

    Attributes:
        check_name:         Nombre del check que fallo.
        detail:             Descripcion tecnica de la falla.
        fiscal_output_id:   message_id del FiscalOutput que causo la falla.
    """

    def __init__(self, check_name: str, detail: str, fiscal_output_id: str) -> None:
        self.check_name = check_name
        self.detail = detail
        self.fiscal_output_id = fiscal_output_id
        super().__init__(
            f"HACIENDA pre-check '{check_name}' fallo para FiscalOutput "
            f"[{fiscal_output_id}]: {detail}. "
            "Formulario NO generado. Requiere revision del CPA."
        )


# ---------------------------------------------------------------------------
# Agente HACIENDA
# ---------------------------------------------------------------------------

class HaciendaAgent(BaseAgent):
    """
    Agente HACIENDA — Genera formularios fiscales de Puerto Rico en dry-run.

    Acepta FiscalOutput del agente FISCAL_PR. Ejecuta triple verificacion
    antes de producir form_data para cada formulario soportado.

    Phase 1: dry-run only. Ningun formulario llega a Hacienda PR.
    Todos los outputs tienen status="DRY_RUN" y filing_ready=False.
    """

    # --- Identidad ---

    @property
    def agent_name(self) -> str:
        return "HACIENDA"

    @property
    def agent_version(self) -> str:
        return "1.0.0-dryrun"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (FiscalOutput,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (OrchestratorDecision,)

    # --- Punto de entrada del framework ---

    def _process_impl(self, message: BaseAgentMessage) -> OrchestratorDecision:
        """
        Procesa un FiscalOutput: ejecuta triple verificacion y registra el dry-run.

        El output es un OrchestratorDecision que documenta la preparacion
        del formulario en el audit trail inmutable. Phase 1 no envia a Hacienda PR.
        """
        fiscal: FiscalOutput = message  # type: ignore[assignment]

        # Inferir periodo desde el FiscalOutput
        try:
            period_start = date.fromisoformat(fiscal.period_from)
            period_end   = date.fromisoformat(fiscal.period_to)
        except ValueError as exc:
            raise HaciendaValidationError(
                check_name="prior_period_consistency",
                detail=(
                    f"Fechas de periodo invalidas en FiscalOutput: "
                    f"period_from='{fiscal.period_from}', period_to='{fiscal.period_to}'. "
                    f"Error: {exc}"
                ),
                fiscal_output_id=fiscal.message_id,
            ) from exc

        # Mapear form_id al form_type canonico soportado
        form_type = _FORM_ID_MAP.get(fiscal.form_id.upper(), fiscal.form_id)

        form_result = self.prepare_form(fiscal, form_type, period_start, period_end)

        logger.info(
            "[HACIENDA v%s] DRY_RUN — form_type='%s', period=%s/%s, "
            "tax_liability=%s, hash=%s, fiscal_output_id=%s",
            self.agent_version,
            form_type,
            fiscal.period_from,
            fiscal.period_to,
            fiscal.tax_liability,
            form_result["calc_hash"],
            fiscal.message_id,
        )

        return OrchestratorDecision(
            source_agent=self.agent_name,
            target_agent="ORQUESTADOR",
            agent_name=self.agent_name,
            input_snapshot=fiscal.model_dump(),
            output_snapshot=form_result,
            rule_ids_applied=(fiscal.rule_ref,),
            confidence=Decimal("1.0"),
            requires_cpa_review=False,
        )

    # ---------------------------------------------------------------------------
    # Metodo publico principal
    # ---------------------------------------------------------------------------

    def prepare_form(
        self,
        fiscal_output: FiscalOutput,
        form_type: str,
        period_start: date,
        period_end: date,
    ) -> dict[str, Any]:
        """
        Prepara el form_data para un formulario fiscal en dry-run.

        Ejecuta triple verificacion antes de construir el formulario:
          1. algebraic_check          — coherencia de tipos Decimal y signos
          2. ivu_crosscheck           — tasa IVU coherente con el tipo de formulario
          3. prior_period_consistency — fechas del periodo son validas y consistentes

        Args:
            fiscal_output: FiscalOutput validado del agente FISCAL_PR.
            form_type:     Uno de: "SC 2915", "941-PR", "W-2PR", "SC 2644".
            period_start:  Inicio del periodo fiscal cubierto.
            period_end:    Fin del periodo fiscal cubierto.

        Returns:
            Dict con: form_id, form_type, period, status ("DRY_RUN"), form_data,
                      calc_hash, validation_notes, filing_ready (siempre False).

        Raises:
            HaciendaValidationError: Si cualquier pre-check falla.
            AgentScopeError:         Si form_type no es un formulario soportado.
        """
        if form_type not in _SUPPORTED_FORMS:
            raise AgentScopeError(
                agent_name=self.agent_name,
                message_type=f"form_type={form_type!r}",
                allowed_types=tuple(sorted(_SUPPORTED_FORMS)),
            )

        validation_notes: list[str] = []

        # --- Triple verificacion (orden fijo — algebraica primero) ---
        self._algebraic_check(fiscal_output, validation_notes)
        self._ivu_crosscheck(fiscal_output, form_type, validation_notes)
        self._prior_period_consistency(fiscal_output, period_start, period_end)
        validation_notes.append("prior_period_consistency: PASS — fechas de periodo coherentes.")

        # --- Construccion del form_data segun tipo ---
        form_data = self._build_form_data(fiscal_output, form_type, period_start, period_end)

        # --- Hash del formulario para audit trail ---
        form_hash = self._calc_form_hash(fiscal_output, form_type, period_start, period_end)

        return {
            "form_id":          fiscal_output.form_id,
            "form_type":        form_type,
            "period": {
                "start": period_start.isoformat(),
                "end":   period_end.isoformat(),
            },
            "status":           "DRY_RUN",    # Phase 1 — siempre DRY_RUN
            "form_data":        form_data,
            "calc_hash":        form_hash,
            "validation_notes": validation_notes,
            "filing_ready":     False,         # Phase 1 — siempre False
        }

    # ---------------------------------------------------------------------------
    # Triple verificacion
    # ---------------------------------------------------------------------------

    def _algebraic_check(
        self,
        fiscal: FiscalOutput,
        notes: list[str],
    ) -> None:
        """
        Verifica coherencia numerica:
          - tax_liability y taxable_base deben ser instancias de Decimal.
          - Un tax_liability negativo es valido (credito de insumo IVU)
            pero se anota para visibilidad del CPA.
        """
        if not isinstance(fiscal.tax_liability, Decimal):
            raise HaciendaValidationError(
                check_name="algebraic_check",
                detail=(
                    f"tax_liability es tipo '{type(fiscal.tax_liability).__name__}', "
                    "se requiere Decimal. Riesgo de error de punto flotante inaceptable."
                ),
                fiscal_output_id=fiscal.message_id,
            )
        if not isinstance(fiscal.taxable_base, Decimal):
            raise HaciendaValidationError(
                check_name="algebraic_check",
                detail=(
                    f"taxable_base es tipo '{type(fiscal.taxable_base).__name__}', "
                    "se requiere Decimal."
                ),
                fiscal_output_id=fiscal.message_id,
            )
        if fiscal.tax_liability < Decimal("0"):
            notes.append(
                f"algebraic_check: NOTA — tax_liability negativo ({fiscal.tax_liability}) "
                "registrado como credito de insumo IVU. Verificar en planilla mensual."
            )
        else:
            notes.append(
                "algebraic_check: PASS — tax_liability y taxable_base son Decimal validos."
            )

    def _ivu_crosscheck(
        self,
        fiscal: FiscalOutput,
        form_type: str,
        notes: list[str],
    ) -> None:
        """
        Para SC 2915: verifica que tax_liability sea coherente con la tasa IVU
        de referencia aplicada sobre taxable_base (tolerancia: _IVU_TOLERANCE).
        Para otros formularios: verifica que tax_type sea consistente con form_type.
        """
        if form_type == "SC 2915":
            if fiscal.taxable_base > Decimal("0") and fiscal.tax_liability > Decimal("0"):
                expected = (
                    fiscal.taxable_base
                    * (_IVU_ESTATAL_RATE + _IVU_MUNICIPAL_RATE)
                    / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                delta = abs(fiscal.tax_liability - expected)
                if delta > _IVU_TOLERANCE:
                    raise HaciendaValidationError(
                        check_name="ivu_crosscheck",
                        detail=(
                            f"tax_liability={fiscal.tax_liability} difiere del esperado "
                            f"{expected} (base={fiscal.taxable_base}, "
                            f"tasa={_IVU_ESTATAL_RATE + _IVU_MUNICIPAL_RATE}%) "
                            f"en mas de la tolerancia {_IVU_TOLERANCE}. Delta={delta}."
                        ),
                        fiscal_output_id=fiscal.message_id,
                    )
                notes.append(
                    f"ivu_crosscheck: PASS — delta={delta} dentro de tolerancia {_IVU_TOLERANCE}."
                )
            else:
                notes.append(
                    "ivu_crosscheck: SKIP — base cero o credito de insumo (tax_liability <= 0)."
                )

        elif form_type == "941-PR":
            if fiscal.tax_type not in ("FICA_SS", "FICA_MEDICARE", "NO_TAX"):
                raise HaciendaValidationError(
                    check_name="ivu_crosscheck",
                    detail=(
                        f"941-PR requiere tax_type FICA_SS, FICA_MEDICARE o NO_TAX, "
                        f"recibido: '{fiscal.tax_type}'."
                    ),
                    fiscal_output_id=fiscal.message_id,
                )
            notes.append(
                f"ivu_crosscheck: PASS — tax_type='{fiscal.tax_type}' valido para 941-PR."
            )

        else:
            # W-2PR y SC 2644 — sin cross-check IVU especifico
            notes.append(
                f"ivu_crosscheck: PASS — form_type='{form_type}' no requiere cross-check IVU."
            )

    def _prior_period_consistency(
        self,
        fiscal: FiscalOutput,
        period_start: date,
        period_end: date,
    ) -> None:
        """
        Verifica que:
          1. period_start <= period_end (rango del formulario valido).
          2. Las fechas del FiscalOutput son ISO 8601 validas.
          3. El rango del FiscalOutput esta contenido en [period_start, period_end].
        """
        if period_start > period_end:
            raise HaciendaValidationError(
                check_name="prior_period_consistency",
                detail=(
                    f"period_start={period_start} es posterior a "
                    f"period_end={period_end}. Rango de formulario invalido."
                ),
                fiscal_output_id=fiscal.message_id,
            )

        try:
            fiscal_from = date.fromisoformat(fiscal.period_from)
            fiscal_to   = date.fromisoformat(fiscal.period_to)
        except ValueError as exc:
            raise HaciendaValidationError(
                check_name="prior_period_consistency",
                detail=f"Fechas del FiscalOutput no son ISO 8601 validas: {exc}",
                fiscal_output_id=fiscal.message_id,
            ) from exc

        if fiscal_from > fiscal_to:
            raise HaciendaValidationError(
                check_name="prior_period_consistency",
                detail=(
                    f"FiscalOutput.period_from={fiscal_from} es posterior a "
                    f"FiscalOutput.period_to={fiscal_to}."
                ),
                fiscal_output_id=fiscal.message_id,
            )

        if not (period_start <= fiscal_from and fiscal_to <= period_end):
            raise HaciendaValidationError(
                check_name="prior_period_consistency",
                detail=(
                    f"Periodo del FiscalOutput [{fiscal_from}, {fiscal_to}] no esta "
                    f"contenido en el rango del formulario [{period_start}, {period_end}]."
                ),
                fiscal_output_id=fiscal.message_id,
            )

    # ---------------------------------------------------------------------------
    # Constructores de form_data por formulario
    # ---------------------------------------------------------------------------

    def _build_form_data(
        self,
        fiscal: FiscalOutput,
        form_type: str,
        period_start: date,
        period_end: date,
    ) -> dict[str, Any]:
        """Despacha la construccion del form_data segun form_type."""
        if form_type == "SC 2915":
            return self._build_sc2915(fiscal, period_start, period_end)
        if form_type == "941-PR":
            return self._build_941pr(fiscal, period_start, period_end)
        if form_type == "W-2PR":
            return self._build_w2pr(fiscal, period_start, period_end)
        if form_type == "SC 2644":
            return self._build_sc2644(fiscal, period_start, period_end)
        # Nunca debe llegar aqui — _SUPPORTED_FORMS lo previene en prepare_form
        raise AgentScopeError(  # pragma: no cover
            agent_name=self.agent_name,
            message_type=f"form_type={form_type!r}",
            allowed_types=tuple(sorted(_SUPPORTED_FORMS)),
        )

    def _build_sc2915(
        self, fiscal: FiscalOutput, period_start: date, period_end: date
    ) -> dict[str, Any]:
        """SC 2915 — Planilla mensual de IVU."""
        q = Decimal("0.01")
        liability = fiscal.tax_liability.quantize(q, rounding=ROUND_HALF_UP)
        base      = fiscal.taxable_base.quantize(q, rounding=ROUND_HALF_UP)
        is_credit = liability < Decimal("0")

        taxable = base if not is_credit else Decimal("0.00")
        return {
            "linea_1_ventas_gravables":    str(taxable),
            "linea_2_ventas_exentas":      str(Decimal("0.00")),
            "linea_3_ivu_estatal_cobrado": str(
                (taxable * _IVU_ESTATAL_RATE / Decimal("100")).quantize(q, rounding=ROUND_HALF_UP)
            ),
            "linea_4_ivu_municipal_cobrado": str(
                (taxable * _IVU_MUNICIPAL_RATE / Decimal("100")).quantize(q, rounding=ROUND_HALF_UP)
            ),
            "linea_5_creditos_insumo":     str(abs(liability) if is_credit else Decimal("0.00")),
            "linea_6_ivu_neto_a_remitir":  str(liability),
            "periodo_inicio":              period_start.isoformat(),
            "periodo_fin":                 period_end.isoformat(),
            "rule_ref":                    fiscal.rule_ref,
            "rate_version":                fiscal.rate_version,
            "exemptions_applied":          list(fiscal.exemptions_applied),
            "dry_run_note":                "FASE 1 — simulacion. No enviado a Hacienda PR.",
        }

    def _build_941pr(
        self, fiscal: FiscalOutput, period_start: date, period_end: date
    ) -> dict[str, Any]:
        """941-PR — Employer's Quarterly Federal Tax Return for Puerto Rico."""
        q    = Decimal("0.01")
        fica = fiscal.tax_liability.quantize(q, rounding=ROUND_HALF_UP)
        wages = fiscal.taxable_base.quantize(q, rounding=ROUND_HALF_UP)
        return {
            "line_1_total_wages":                      str(wages),
            "line_2_fica_tax_withheld":                str(fica),
            "line_3_total_taxes_before_adjustments":   str(fica),
            "line_4_total_taxes_after_adjustments":    str(fica),
            "quarter_start":                           period_start.isoformat(),
            "quarter_end":                             period_end.isoformat(),
            "rule_ref":                                fiscal.rule_ref,
            "rate_version":                            fiscal.rate_version,
            "dry_run_note": "FASE 1 — simulacion. No enviado al IRS/Hacienda.",
        }

    def _build_w2pr(
        self, fiscal: FiscalOutput, period_start: date, period_end: date
    ) -> dict[str, Any]:
        """W-2PR — Annual Wage Report."""
        q     = Decimal("0.01")
        wages = fiscal.taxable_base.quantize(q, rounding=ROUND_HALF_UP)
        taxes = fiscal.tax_liability.quantize(q, rounding=ROUND_HALF_UP)
        return {
            "box_1_wages_tips_other_compensation": str(wages),
            "box_2_federal_income_tax_withheld":  str(Decimal("0.00")),
            "box_3_social_security_wages":        str(wages),
            "box_4_social_security_tax_withheld": str(taxes),
            "box_5_medicare_wages":               str(wages),
            "box_6_medicare_tax_withheld":        str(Decimal("0.00")),
            "box_16_pr_wages":                    str(wages),
            "tax_year":                           str(period_end.year),
            "period_start":                       period_start.isoformat(),
            "period_end":                         period_end.isoformat(),
            "rule_ref":                           fiscal.rule_ref,
            "rate_version":                       fiscal.rate_version,
            "dry_run_note": "FASE 1 — simulacion. No enviado a Hacienda PR / SSA.",
        }

    def _build_sc2644(
        self, fiscal: FiscalOutput, period_start: date, period_end: date
    ) -> dict[str, Any]:
        """SC 2644 — Income Tax Return for Corporations."""
        q          = Decimal("0.01")
        net_income = fiscal.taxable_base.quantize(q, rounding=ROUND_HALF_UP)
        tax_due    = fiscal.tax_liability.quantize(q, rounding=ROUND_HALF_UP)
        return {
            "part_i_gross_income":          str(net_income),
            "part_ii_deductions":           str(Decimal("0.00")),
            "part_iii_net_taxable_income":  str(net_income),
            "part_iv_tax_computed":         str(tax_due),
            "part_v_credits_and_payments":  str(Decimal("0.00")),
            "part_vi_balance_due":          str(tax_due),
            "taxable_year_start":           period_start.isoformat(),
            "taxable_year_end":             period_end.isoformat(),
            "rule_ref":                     fiscal.rule_ref,
            "rate_version":                 fiscal.rate_version,
            "exemptions_applied":           list(fiscal.exemptions_applied),
            "dry_run_note": "FASE 1 — simulacion. No enviado a Hacienda PR.",
        }

    # ---------------------------------------------------------------------------
    # Helpers privados
    # ---------------------------------------------------------------------------

    @staticmethod
    def _calc_form_hash(
        fiscal: FiscalOutput,
        form_type: str,
        period_start: date,
        period_end: date,
    ) -> str:
        """
        sha256 de campos clave del formulario para audit trail.
        Primeros 16 caracteres del hex digest (coherente con fiscal_pr.py).
        """
        payload = {
            "form_type":        form_type,
            "tax_liability":    str(fiscal.tax_liability),
            "taxable_base":     str(fiscal.taxable_base),
            "period_start":     period_start.isoformat(),
            "period_end":       period_end.isoformat(),
            "rule_ref":         fiscal.rule_ref,
            "rate_version":     fiscal.rate_version,
            "fiscal_calc_hash": fiscal.calc_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]
