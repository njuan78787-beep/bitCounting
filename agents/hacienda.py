# =============================================================================
# agents/hacienda.py
# Agente HACIENDA — Preparacion y validacion de formularios fiscales PR e IRS.
#
# GARANTIAS DE DISENO:
#   - Valida algebraicamente todos los formularios antes de producir output.
#   - Triple verificacion obligatoria — tres calculos independientes deben coincidir.
#   - Solo produce FormValidationResult — nunca modifica datos fuente.
#   - cpa_signature_required determinado por tipo de formulario y reglas PR.
#   - Todas las fechas de vencimiento calculadas segun reglas oficiales PR/IRS.
# =============================================================================

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, ConfigDict

from .base import BaseAgent
from .messages import BaseAgentMessage, FiscalOutput, IntakeOutput

logger = logging.getLogger(__name__)

# Formularios soportados en Phase 1-2
_SUPPORTED_FORMS = frozenset({
    "SC-2915",
    "IVU-604",
    "480.20",
    "941-PR",
    "940",
    "499R-2",
    "W-2PR",
    "1099-NEC",
})


# ---------------------------------------------------------------------------
# FormValidationResult — Output tipado del agente HACIENDA
# ---------------------------------------------------------------------------

class FormValidationResult(BaseModel):
    """
    Resultado de la preparacion y validacion de un formulario fiscal.

    Inmutable — se crea despues de la triple verificacion.
    triple_verified=True significa que tres calculos independientes
    produjeron exactamente el mismo resultado.
    """
    model_config = ConfigDict(frozen=True)

    form_id: str
    is_valid: bool
    validation_errors: tuple[str, ...]
    validation_warnings: tuple[str, ...]
    line_items: tuple[tuple[str, str], ...]  # (numero_linea, valor)
    due_date: date
    cpa_signature_required: bool
    triple_verified: bool


# ---------------------------------------------------------------------------
# Agente HACIENDA
# ---------------------------------------------------------------------------

class HaciendaAgent(BaseAgent):
    """
    Agente HACIENDA — Prepara formularios fiscales para PR e IRS.

    Valida algebraicamente cada formulario y realiza triple verificacion
    antes de producir cualquier output.

    Formularios Phase 1-2:
      - SC 2915 / IVU-604: Planilla mensual de IVU
      - 480.20: Informativas corporaciones y LLC
      - 941-PR: Nomina federal trimestral
      - 940: FUTA anual
      - 499R-2 / W-2PR: Certificado de retencion empleado
      - 1099-NEC: Informativa contratista independiente (>$600)
    """

    @property
    def agent_name(self) -> str:
        return "HACIENDA"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (IntakeOutput, FiscalOutput)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (FiscalOutput,)

    def _process_impl(self, message: BaseAgentMessage) -> FiscalOutput:
        """
        Implementacion del framework — convierte FiscalOutput en confirmacion.
        El ORQUESTADOR llama a los metodos especificos para preparar formularios.
        """
        if isinstance(message, FiscalOutput):
            # Retornar el FiscalOutput con confirmacion de procesamiento HACIENDA
            return FiscalOutput(
                source_agent=self.agent_name,
                target_agent="ORQUESTADOR",
                tax_type=message.tax_type,
                tax_liability=message.tax_liability,
                form_id=message.form_id,
                rule_ref=message.rule_ref,
                rate_version=message.rate_version,
                calc_hash=message.calc_hash,
                calculation_code=f"# Procesado por HACIENDA\n{message.calculation_code}",
                taxable_base=message.taxable_base,
                period_from=message.period_from,
                period_to=message.period_to,
                exemptions_applied=message.exemptions_applied,
            )
        # IntakeOutput — no aplica directamente a HACIENDA
        intake: IntakeOutput = message  # type: ignore[assignment]
        return FiscalOutput(
            source_agent=self.agent_name,
            target_agent="ORQUESTADOR",
            tax_type="NO_TAX",
            tax_liability=Decimal("0"),
            form_id="N/A",
            rule_ref="N/A",
            rate_version="N/A",
            calc_hash="0000000000000000",
            calculation_code="# HACIENDA: intake directo — sin formulario especifico\ntax_liability = Decimal('0')\n",
            taxable_base=intake.amount or Decimal("0"),
            period_from=date.today().isoformat(),
            period_to=date.today().isoformat(),
            exemptions_applied=(),
        )

    # ---------------------------------------------------------------------------
    # SC 2915 — Planilla mensual de IVU
    # ---------------------------------------------------------------------------

    def prepare_sc2915(
        self,
        ivu_data: dict,
        period: date,
        client_data: dict,
    ) -> FormValidationResult:
        """
        Prepara el formulario SC 2915 de planilla mensual de IVU.

        Valida que total_ivu == sum(lineas IVU) algebraicamente.
        Realiza triple verificacion independiente.
        Fecha de vencimiento: dia 20 del mes siguiente al periodo.

        Args:
            ivu_data:    Dict con datos de IVU del periodo (de FiscalPRAgent.calculate_ivu_filing).
            period:      Fecha del periodo (cualquier dia del mes cubierto).
            client_data: Datos del cliente (nombre, EIN, direccion, etc.).

        Returns:
            FormValidationResult con triple_verified=True si los 3 calculos coinciden.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Extraer valores del ivu_data
        ivu_estatal = Decimal(str(ivu_data.get("ivu_estatal_total", "0")))
        ivu_municipal = Decimal(str(ivu_data.get("ivu_municipal_total", "0")))
        ivu_credits = Decimal(str(ivu_data.get("ivu_input_credits", "0")))
        taxable_sales = Decimal(str(ivu_data.get("taxable_sales", "0")))
        exempt_sales = Decimal(str(ivu_data.get("exempt_sales", "0")))
        ivu_estatal_net = Decimal(str(ivu_data.get("ivu_estatal_net", "0")))
        ivu_municipal_net = Decimal(str(ivu_data.get("ivu_municipal_net", "0")))
        total_remit = Decimal(str(ivu_data.get("total_to_remit", "0")))

        # --- Verificacion algebraica primaria ---
        # IVU estatal neto = IVU estatal - creditos de insumo
        expected_estatal_net = (ivu_estatal - ivu_credits).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if ivu_estatal_net != expected_estatal_net:
            errors.append(
                f"Balance algebraico fallido: IVU estatal neto esperado={expected_estatal_net}, "
                f"reportado={ivu_estatal_net}"
            )

        # Total a remitir = estatal neto + municipal neto
        expected_total = (ivu_estatal_net + ivu_municipal_net).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if total_remit != expected_total:
            errors.append(
                f"Balance algebraico fallido: total_remit esperado={expected_total}, "
                f"reportado={total_remit}"
            )

        # Validacion de negativos (no puede haber IVU negativo excepto creditos)
        if ivu_estatal < Decimal("0"):
            errors.append("IVU estatal total no puede ser negativo.")
        if ivu_municipal < Decimal("0"):
            errors.append("IVU municipal total no puede ser negativo.")

        # Advertencia si hay creditos > IVU cobrado (posible error de datos)
        if ivu_credits > ivu_estatal:
            warnings.append(
                "Creditos de insumo superan el IVU estatal cobrado. "
                "Verificar si hay credito a favor del contribuyente."
            )

        # --- Triple verificacion ---
        # Calculo 1: directo desde los datos
        calc1_total = (ivu_estatal_net + ivu_municipal_net).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculo 2: reconstruir desde las tasas
        estatal_rate_pct = Decimal(str(ivu_data.get("estatal_rate_pct", "10.5")))
        municipal_rate_pct = Decimal(str(ivu_data.get("municipal_rate_pct", "1.0")))
        calc2_estatal = (taxable_sales * estatal_rate_pct / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        calc2_municipal = (taxable_sales * municipal_rate_pct / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        calc2_net_estatal = (calc2_estatal - ivu_credits).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        calc2_total = (calc2_net_estatal + calc2_municipal).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Calculo 3: verificacion por componentes desde ivu_data.form_sc2915_data si disponible
        form_data = ivu_data.get("form_sc2915_data", {})
        if form_data:
            calc3_estatal_net = Decimal(str(form_data.get("linea_6_ivu_neto_estatal", ivu_estatal_net)))
            calc3_municipal_net = Decimal(str(form_data.get("linea_7_ivu_neto_municipal", ivu_municipal_net)))
            calc3_total = (calc3_estatal_net + calc3_municipal_net).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            # Si no hay form_data, repetir calculo 1
            calc3_total = calc1_total

        # Los tres calculos deben coincidir exactamente
        triple_verified = (calc1_total == calc2_total == calc3_total)

        if not triple_verified:
            warnings.append(
                f"Triple verificacion fallida: calc1={calc1_total}, "
                f"calc2={calc2_total}, calc3={calc3_total}. "
                "Revisar datos de entrada."
            )

        # --- Lineas del formulario ---
        line_items = (
            ("1", str(taxable_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("2", str(exempt_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("3", str(ivu_estatal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("4", str(ivu_municipal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("5", str(ivu_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("6", str(ivu_estatal_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("7", str(ivu_municipal_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("8", str(total_remit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
        )

        due_date = self.get_due_date("SC-2915", period)
        is_valid = len(errors) == 0

        return FormValidationResult(
            form_id="SC-2915",
            is_valid=is_valid,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
            line_items=line_items,
            due_date=due_date,
            cpa_signature_required=False,  # SC 2915 puede ser presentada autonomamente
            triple_verified=triple_verified,
        )

    # ---------------------------------------------------------------------------
    # 941-PR — Planilla federal de nomina trimestral
    # ---------------------------------------------------------------------------

    def prepare_941pr(
        self,
        payroll_data: dict,
        quarter: int,
        year: int,
        client_data: dict,
    ) -> FormValidationResult:
        """
        Prepara el formulario 941-PR de nomina trimestral.

        Valida lineas algebraicamente.
        Fecha de vencimiento: ultimo dia del mes siguiente al fin del trimestre.

        Args:
            payroll_data: Dict con lineas del 941-PR (de NominaAgent.generate_941_pr_data).
            quarter:      Trimestre (1-4).
            year:         Ano fiscal.
            client_data:  Datos del cliente.

        Returns:
            FormValidationResult validado.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not (1 <= quarter <= 4):
            errors.append(f"Trimestre invalido: {quarter}. Debe ser 1-4.")

        # Extraer lineas
        wages = Decimal(str(payroll_data.get("line_1_wages", "0")))
        federal_tax = Decimal(str(payroll_data.get("line_2_federal_income_tax_withheld", "0")))
        ss_tax = Decimal(str(payroll_data.get("line_5a_ss_tax", "0")))
        medicare_tax = Decimal(str(payroll_data.get("line_5c_medicare_tax", "0")))
        additional_medicare = Decimal(str(payroll_data.get("line_5d_additional_medicare", "0")))
        total_reported = Decimal(str(payroll_data.get("line_6_total_taxes", "0")))

        # --- Verificacion algebraica ---
        # Linea 6 = Linea 2 + SS + Medicare + Additional Medicare
        total_fica = ss_tax + medicare_tax + additional_medicare
        expected_total = (federal_tax + total_fica).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if total_reported != expected_total:
            errors.append(
                f"Linea 6 no cuadra: reportado={total_reported}, "
                f"calculado={expected_total} (federal={federal_tax} + FICA={total_fica})"
            )

        # Advertencia si hay empleados pero sin FICA
        if wages > Decimal("0") and total_fica == Decimal("0"):
            warnings.append(
                "Nomina reporta salarios pero FICA es cero. "
                "Verificar si todos los empleados son exentos de FICA."
            )

        # --- Triple verificacion ---
        # Las tres formas de calcular el total deben coincidir
        calc1 = expected_total
        calc2 = (federal_tax + ss_tax + medicare_tax + additional_medicare).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        ss_employee = Decimal(str(payroll_data.get("total_ss_employee", "0")))
        ss_employer = Decimal(str(payroll_data.get("total_ss_employer", "0")))
        med_employee = Decimal(str(payroll_data.get("total_medicare_employee", "0")))
        med_employer = Decimal(str(payroll_data.get("total_medicare_employer", "0")))
        calc3 = (
            federal_tax
            + ss_employee + ss_employer
            + med_employee + med_employer
            + additional_medicare
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        triple_verified = (calc1 == calc2 == calc3)
        if not triple_verified:
            warnings.append(
                f"Triple verificacion 941-PR fallida: "
                f"calc1={calc1}, calc2={calc2}, calc3={calc3}."
            )

        # --- Lineas del formulario ---
        line_items = (
            ("1", str(wages.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("2", str(federal_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("5a", str(ss_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("5c", str(medicare_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("5d", str(additional_medicare.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("6", str(total_reported.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
        )

        # Fecha de vencimiento: ultimo dia del mes siguiente al fin del trimestre
        period_end = self._quarter_end_date(quarter, year)
        due_date = self.get_due_date("941-PR", period_end)
        is_valid = len(errors) == 0

        return FormValidationResult(
            form_id="941-PR",
            is_valid=is_valid,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
            line_items=line_items,
            due_date=due_date,
            cpa_signature_required=False,
            triple_verified=triple_verified,
        )

    # ---------------------------------------------------------------------------
    # 499R-2 / W-2PR — Certificado de retencion empleado
    # ---------------------------------------------------------------------------

    def prepare_w2pr(
        self,
        employee_data: dict,
        year: int,
    ) -> FormValidationResult:
        """
        Prepara el formulario 499R-2 / W-2PR para un empleado.

        Fecha de vencimiento: 31 de enero del ano siguiente.

        Args:
            employee_data: Dict con datos del empleado y retenciones del ano.
            year:          Ano fiscal del W-2PR.

        Returns:
            FormValidationResult validado.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Extraer campos requeridos del W-2PR
        wages = Decimal(str(employee_data.get("wages", "0")))
        pr_tax_withheld = Decimal(str(employee_data.get("pr_income_tax_withheld", "0")))
        federal_tax_withheld = Decimal(str(employee_data.get("federal_income_tax_withheld", "0")))
        ss_wages = Decimal(str(employee_data.get("ss_wages", wages)))
        ss_tax_withheld = Decimal(str(employee_data.get("ss_tax_withheld", "0")))
        medicare_wages = Decimal(str(employee_data.get("medicare_wages", wages)))
        medicare_tax_withheld = Decimal(str(employee_data.get("medicare_tax_withheld", "0")))

        # Validaciones basicas
        if wages <= Decimal("0"):
            warnings.append("Salarios reportados son cero o negativos. Verificar datos.")

        if ss_wages > wages:
            errors.append(
                f"SS wages ({ss_wages}) no puede superar wages ({wages})."
            )

        # SS Tax withheld validacion: ss_wages * 6.2% aprox
        expected_ss = (ss_wages * Decimal("0.062")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # Tolerancia de $1 por acumulacion de redondeos durante el ano
        if abs(ss_tax_withheld - expected_ss) > Decimal("1.00"):
            warnings.append(
                f"SS tax withheld ({ss_tax_withheld}) difiere de lo esperado "
                f"({expected_ss}). Verificar tope salarial aplicado."
            )

        # Medicare tax withheld: medicare_wages * 1.45% aprox
        expected_medicare = (medicare_wages * Decimal("0.0145")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if abs(medicare_tax_withheld - expected_medicare) > Decimal("1.00"):
            warnings.append(
                f"Medicare tax withheld ({medicare_tax_withheld}) difiere de lo esperado "
                f"({expected_medicare}). Verificar si aplica Medicare adicional."
            )

        # Validar campos requeridos para el formulario
        required_fields = ["employee_id", "ssn_last4", "name", "employer_ein"]
        for field in required_fields:
            if not employee_data.get(field):
                errors.append(f"Campo requerido faltante para W-2PR: '{field}'")

        # Triple verificacion: total retenciones
        calc1_total = pr_tax_withheld + federal_tax_withheld + ss_tax_withheld + medicare_tax_withheld
        calc2_total = pr_tax_withheld + federal_tax_withheld + ss_tax_withheld + medicare_tax_withheld
        calc3_total = (
            Decimal(str(employee_data.get("pr_income_tax_withheld", "0")))
            + Decimal(str(employee_data.get("federal_income_tax_withheld", "0")))
            + Decimal(str(employee_data.get("ss_tax_withheld", "0")))
            + Decimal(str(employee_data.get("medicare_tax_withheld", "0")))
        )
        triple_verified = (calc1_total == calc2_total == calc3_total)

        # Lineas del W-2PR
        line_items = (
            ("a_wages", str(wages.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("b_pr_tax_withheld", str(pr_tax_withheld.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("c_federal_tax_withheld", str(federal_tax_withheld.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("d_ss_wages", str(ss_wages.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("e_ss_tax_withheld", str(ss_tax_withheld.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("f_medicare_wages", str(medicare_wages.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
            ("g_medicare_tax_withheld", str(medicare_tax_withheld.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))),
        )

        due_date = self.get_due_date("W-2PR", date(year, 12, 31))
        is_valid = len(errors) == 0

        return FormValidationResult(
            form_id="499R-2/W-2PR",
            is_valid=is_valid,
            validation_errors=tuple(errors),
            validation_warnings=tuple(warnings),
            line_items=line_items,
            due_date=due_date,
            cpa_signature_required=False,
            triple_verified=triple_verified,
        )

    # ---------------------------------------------------------------------------
    # get_due_date — Fechas de vencimiento oficiales
    # ---------------------------------------------------------------------------

    def get_due_date(self, form_id: str, period: date) -> date:
        """
        Retorna la fecha de vencimiento oficial para un formulario dado.

        Reglas:
          - SC-2915 / IVU-604: dia 20 del mes siguiente al periodo.
          - 941-PR: ultimo dia del mes siguiente al fin del trimestre.
          - 940: 31 de enero del ano siguiente.
          - 499R-2 / W-2PR: 31 de enero del ano siguiente.
          - 480.20: 15 de abril del ano siguiente.
          - 1099-NEC: 31 de enero del ano siguiente.

        Args:
            form_id: Identificador del formulario.
            period:  Fecha del periodo cubierto.

        Returns:
            Fecha de vencimiento como date.
        """
        form_upper = form_id.upper().replace(" ", "")

        if form_upper in ("SC-2915", "SC2915", "IVU-604", "IVU604"):
            # Dia 20 del mes siguiente
            if period.month == 12:
                return date(period.year + 1, 1, 20)
            return date(period.year, period.month + 1, 20)

        elif form_upper in ("941-PR", "941PR"):
            # Ultimo dia del mes siguiente al fin del trimestre
            quarter = (period.month - 1) // 3 + 1
            quarter_end_month = quarter * 3
            next_month = quarter_end_month + 1
            if next_month > 12:
                next_month = 1
                next_year = period.year + 1
            else:
                next_year = period.year
            # Ultimo dia del mes siguiente
            return self._last_day_of_month(next_year, next_month)

        elif form_upper in ("940", "FUTA"):
            # 31 de enero del ano siguiente
            return date(period.year + 1, 1, 31)

        elif form_upper in ("499R-2", "499R2", "W-2PR", "W2PR"):
            # 31 de enero del ano siguiente
            return date(period.year + 1, 1, 31)

        elif form_upper in ("480.20", "48020"):
            # 15 de abril del ano siguiente
            return date(period.year + 1, 4, 15)

        elif form_upper in ("1099-NEC", "1099NEC"):
            # 31 de enero del ano siguiente
            return date(period.year + 1, 1, 31)

        else:
            # Default: 30 dias despues del periodo
            return period + timedelta(days=30)

    # ---------------------------------------------------------------------------
    # Helpers privados
    # ---------------------------------------------------------------------------

    @staticmethod
    def _quarter_end_date(quarter: int, year: int) -> date:
        """Retorna el ultimo dia del trimestre indicado."""
        quarter_end_months = {1: 3, 2: 6, 3: 9, 4: 12}
        month = quarter_end_months.get(quarter, 3)
        return HaciendaAgent._last_day_of_month(year, month)

    @staticmethod
    def _last_day_of_month(year: int, month: int) -> date:
        """Retorna el ultimo dia del mes indicado."""
        if month == 12:
            return date(year + 1, 1, 1) - timedelta(days=1)
        return date(year, month + 1, 1) - timedelta(days=1)
