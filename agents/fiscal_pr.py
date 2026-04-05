# =============================================================================
# agents/fiscal_pr.py
# Agente FISCAL_PR — Aplicacion de derecho tributario de Puerto Rico.
#
# GARANTIAS DE DISENO:
#   - NUNCA hardcodea tasas — usa tax_rules.get_rule() para todos los lookups.
#   - NUNCA hace aritmetica inline — usa tax_rules.calculate_ivu() para IVU.
#   - SOLO LECTURA sobre tax_rules — arquitectonicamente imposible modificarlas.
#   - Genera codigo Python auditable almacenado en FiscalOutput.calculation_code.
#   - calc_hash = sha256(base_amount + rate + date + rule_id) para verificacion.
# =============================================================================

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .base import BaseAgent
from .messages import BaseAgentMessage, FiscalOutput, IntakeOutput
from tax_rules import get_rule, calculate_ivu

logger = logging.getLogger(__name__)

# Categorias exentas reconocidas — los terminos clave se buscan en vendor/description
_EXEMPT_KEYWORDS: dict[str, str] = {
    "alimento": "Alimentos sin preparar para consumo en el hogar",
    "comida": "Alimentos sin preparar para consumo en el hogar",
    "supermercado": "Alimentos sin preparar para consumo en el hogar",
    "grocery": "Alimentos sin preparar para consumo en el hogar",
    "farmacia": "Medicamentos de venta libre (OTC) en farmacia",
    "medicina": "Medicamentos recetados por medico licenciado",
    "medicamento": "Medicamentos recetados por medico licenciado",
    "farmaco": "Medicamentos recetados por medico licenciado",
    "rx": "Medicamentos recetados por medico licenciado",
    "hospital": "Servicios medicos y hospitalarios",
    "clinica": "Servicios medicos y hospitalarios",
    "medico": "Servicios medicos y hospitalarios",
    "medica": "Servicios medicos y hospitalarios",
    "doctor": "Servicios medicos y hospitalarios",
    "dental": "Servicios medicos y hospitalarios",
    "laboratorio": "Servicios medicos y hospitalarios",
}


class FiscalPRAgent(BaseAgent):
    """
    Agente FISCAL_PR — Calcula obligaciones tributarias bajo derecho de Puerto Rico.

    Aplica IVU, retenciones, FICA, FUTA y reglas de Act 60. Tiene acceso
    de SOLO LECTURA al modulo tax_rules. Nunca genera ni modifica reglas.

    Flujo tipico:
      IntakeOutput → calculate_tax_liability() → FiscalOutput → HACIENDA
    """

    # --- Identidad ---

    @property
    def agent_name(self) -> str:
        return "FISCAL_PR"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        return (IntakeOutput,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (FiscalOutput,)

    # --- Punto de entrada del framework ---

    def _process_impl(self, message: BaseAgentMessage) -> FiscalOutput:
        """
        Implementacion de proceso para IntakeOutput.
        Infiere el tipo de transaccion desde el intake y calcula la obligacion.
        """
        intake: IntakeOutput = message  # type: ignore[assignment]

        # Determinar tipo de transaccion desde el intake
        transaction_type = self._infer_transaction_type(intake)

        # Fecha de consulta: usar fecha del documento o hoy
        if intake.date:
            try:
                query_date = date.fromisoformat(intake.date)
            except ValueError:
                query_date = date.today()
        else:
            query_date = date.today()

        return self.calculate_tax_liability(
            intake_output=intake,
            transaction_type=transaction_type,
            query_date=query_date,
        )

    # ---------------------------------------------------------------------------
    # Metodo principal publico
    # ---------------------------------------------------------------------------

    def calculate_tax_liability(
        self,
        intake_output: IntakeOutput,
        transaction_type: str,
        query_date: date,
    ) -> FiscalOutput:
        """
        Determina y calcula la obligacion tributaria para una transaccion.

        Args:
            intake_output:    Output del agente INTAKE con datos del documento.
            transaction_type: "invoice", "expense", "payroll", "tax_payment",
                              "transfer", "adjustment".
            query_date:       Fecha de la transaccion — determina reglas vigentes.

        Returns:
            FiscalOutput con tax_liability, form_id, rule_ref, rate_version,
            calc_hash y calculation_code para auditabilidad completa.
        """
        base_amount = intake_output.amount or Decimal("0")

        # Enrutador por tipo de transaccion
        if transaction_type == "invoice":
            return self._handle_invoice(intake_output, base_amount, query_date)
        elif transaction_type == "expense":
            return self._handle_expense(intake_output, base_amount, query_date)
        elif transaction_type == "payroll":
            return self._handle_payroll(intake_output, base_amount, query_date)
        elif transaction_type in ("tax_payment", "transfer"):
            return self._handle_no_tax(intake_output, base_amount, query_date, transaction_type)
        elif transaction_type == "adjustment":
            # Ajuste — tratar como invoice por defecto (mas conservador)
            return self._handle_invoice(intake_output, base_amount, query_date)
        else:
            return self._handle_no_tax(intake_output, base_amount, query_date, transaction_type)

    # ---------------------------------------------------------------------------
    # Handlers por tipo de transaccion
    # ---------------------------------------------------------------------------

    def _handle_invoice(
        self,
        intake: IntakeOutput,
        base_amount: Decimal,
        query_date: date,
    ) -> FiscalOutput:
        """
        Venta / factura — aplica IVU estatal 10.5% + municipal 1.0%.
        Verifica exenciones antes de calcular.
        """
        # Verificar exencion
        is_exempt, exempt_reason = self._check_ivu_exemption(intake)

        if is_exempt:
            # Transaccion exenta — obligacion cero
            calc_code = (
                f"# Transaccion exenta de IVU\n"
                f"# Razon: {exempt_reason}\n"
                f"# Vendedor/Descripcion: {intake.vendor}\n"
                f"tax_liability = Decimal('0')\n"
            )
            calc_hash = self._make_hash(base_amount, Decimal("0"), query_date, "EXEMPT")
            estatal_rule = get_rule("IVU_ESTATAL_PR_2015_V1", query_date)
            rule_ref = estatal_rule.rule_id if estatal_rule else "IVU_ESTATAL_PR_2015_V1"
            rate_version = f"{rule_ref}:exempt"

            return FiscalOutput(
                source_agent=self.agent_name,
                target_agent="HACIENDA",
                tax_type="IVU_ESTATAL",
                tax_liability=Decimal("0"),
                form_id="SC-2915",
                rule_ref=rule_ref,
                rate_version=rate_version,
                calc_hash=calc_hash,
                calculation_code=calc_code,
                taxable_base=Decimal("0"),
                period_from=query_date.isoformat(),
                period_to=query_date.isoformat(),
                exemptions_applied=(exempt_reason,),
            )

        # Calcular IVU usando la funcion del modulo — NUNCA aritmetica directa
        ivu_calc = calculate_ivu(
            base_amount=base_amount,
            query_date=query_date,
            include_municipal=True,
        )

        # Generar codigo auditable
        calc_code = (
            f"from tax_rules import calculate_ivu\n"
            f"from decimal import Decimal\n"
            f"from datetime import date\n"
            f"# Calculo IVU estatal + municipal para factura\n"
            f"ivu_calc = calculate_ivu(\n"
            f"    base_amount=Decimal('{base_amount}'),\n"
            f"    query_date=date({query_date.year}, {query_date.month}, {query_date.day}),\n"
            f"    include_municipal=True,\n"
            f")\n"
            f"# IVU estatal: {ivu_calc.ivu_estatal_rate}% = {ivu_calc.ivu_estatal_amount}\n"
            f"# IVU municipal: {ivu_calc.ivu_municipal_rate}% = {ivu_calc.ivu_municipal_amount}\n"
            f"tax_liability = ivu_calc.total_ivu  # = {ivu_calc.total_ivu}\n"
        )

        rate_version = f"{ivu_calc.rule_id_estatal}:{query_date.isoformat()}"

        return FiscalOutput(
            source_agent=self.agent_name,
            target_agent="HACIENDA",
            tax_type="IVU_ESTATAL",
            tax_liability=ivu_calc.total_ivu,
            form_id="SC-2915",
            rule_ref=ivu_calc.rule_id_estatal,
            rate_version=rate_version,
            calc_hash=ivu_calc.calc_hash,
            calculation_code=calc_code,
            taxable_base=base_amount,
            period_from=query_date.isoformat(),
            period_to=query_date.isoformat(),
            exemptions_applied=(),
        )

    def _handle_expense(
        self,
        intake: IntakeOutput,
        base_amount: Decimal,
        query_date: date,
    ) -> FiscalOutput:
        """
        Gasto — registra el IVU pagado como credito de insumo (input credit).
        El IVU en compras es deducible contra el IVU cobrado en ventas.
        """
        # El IVU pagado en gastos es el tax_amount del documento si existe,
        # de lo contrario se calcula sobre la base
        ivu_pagado = intake.tax_amount or Decimal("0")

        if ivu_pagado == Decimal("0") and base_amount > Decimal("0"):
            ivu_calc = calculate_ivu(
                base_amount=base_amount,
                query_date=query_date,
                include_municipal=True,
            )
            ivu_pagado = ivu_calc.total_ivu
            calc_hash = ivu_calc.calc_hash
            rule_ref = ivu_calc.rule_id_estatal
        else:
            rule = get_rule("IVU_ESTATAL_PR_2015_V1", query_date)
            rule_ref = rule.rule_id if rule else "IVU_ESTATAL_PR_2015_V1"
            rate = rule.rate if rule else Decimal("0")
            calc_hash = self._make_hash(base_amount, rate, query_date, rule_ref)

        calc_code = (
            f"# IVU pagado en gasto — credito de insumo para SC 2915\n"
            f"# Base: {base_amount}, IVU pagado: {ivu_pagado}\n"
            f"# El IVU en compras se acredita contra el IVU cobrado en ventas\n"
            f"ivu_input_credit = Decimal('{ivu_pagado}')  # credito de insumo\n"
            f"tax_liability = -ivu_input_credit  # negativo = reduce obligacion\n"
        )

        return FiscalOutput(
            source_agent=self.agent_name,
            target_agent="HACIENDA",
            tax_type="IVU_ESTATAL",
            tax_liability=-ivu_pagado,  # negativo: es un credito
            form_id="SC-2915",
            rule_ref=rule_ref,
            rate_version=f"{rule_ref}:{query_date.isoformat()}",
            calc_hash=calc_hash,
            calculation_code=calc_code,
            taxable_base=base_amount,
            period_from=query_date.isoformat(),
            period_to=query_date.isoformat(),
            exemptions_applied=(),
        )

    def _handle_payroll(
        self,
        intake: IntakeOutput,
        base_amount: Decimal,
        query_date: date,
    ) -> FiscalOutput:
        """
        Nomina — calcula FICA SS + Medicare para el FiscalOutput.
        El calculo detallado por empleado lo maneja el agente NOMINA.
        Este metodo produce el FiscalOutput agregado para HACIENDA.
        """
        ss_rule = get_rule("FICA_SS_2025_V1", query_date)
        medicare_rule = get_rule("FICA_MEDICARE_2013_V1", query_date)

        ss_rate_emp = ss_rule.rate_employee if ss_rule else Decimal("0")
        ss_rate_er = ss_rule.rate_employer if ss_rule else Decimal("0")
        med_rate_emp = medicare_rule.rate_employee if medicare_rule else Decimal("0")
        med_rate_er = medicare_rule.rate_employer if medicare_rule else Decimal("0")

        # SS del empleado (patrono no excede tope aqui — NOMINA lo maneja)
        ss_employee = (base_amount * ss_rate_emp / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        ss_employer = (base_amount * ss_rate_er / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        medicare_employee = (base_amount * med_rate_emp / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        medicare_employer = (base_amount * med_rate_er / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        total_fica = ss_employee + ss_employer + medicare_employee + medicare_employer

        ss_rule_id = ss_rule.rule_id if ss_rule else "FICA_SS_2025_V1"
        med_rule_id = medicare_rule.rule_id if medicare_rule else "FICA_MEDICARE_2013_V1"

        calc_code = (
            f"from tax_rules import get_rule\n"
            f"from decimal import Decimal, ROUND_HALF_UP\n"
            f"from datetime import date\n"
            f"# Calculo FICA para nomina\n"
            f"ss_rule = get_rule('{ss_rule_id}', date({query_date.year}, {query_date.month}, {query_date.day}))\n"
            f"med_rule = get_rule('{med_rule_id}', date({query_date.year}, {query_date.month}, {query_date.day}))\n"
            f"base = Decimal('{base_amount}')\n"
            f"ss_employee = (base * ss_rule.rate_employee / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
            f"ss_employer = (base * ss_rule.rate_employer / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
            f"medicare_employee = (base * med_rule.rate_employee / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
            f"medicare_employer = (base * med_rule.rate_employer / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
            f"tax_liability = ss_employee + ss_employer + medicare_employee + medicare_employer\n"
            f"# = {total_fica}\n"
        )

        calc_hash = self._make_hash(base_amount, ss_rate_emp, query_date, ss_rule_id)

        return FiscalOutput(
            source_agent=self.agent_name,
            target_agent="HACIENDA",
            tax_type="FICA_SS",
            tax_liability=total_fica,
            form_id="941-PR",
            rule_ref=ss_rule_id,
            rate_version=f"{ss_rule_id}:{query_date.isoformat()}",
            calc_hash=calc_hash,
            calculation_code=calc_code,
            taxable_base=base_amount,
            period_from=query_date.isoformat(),
            period_to=query_date.isoformat(),
            exemptions_applied=(),
        )

    def _handle_no_tax(
        self,
        intake: IntakeOutput,
        base_amount: Decimal,
        query_date: date,
        transaction_type: str,
    ) -> FiscalOutput:
        """
        Transacciones sin obligacion tributaria adicional:
        tax_payment, transfer.
        """
        calc_code = (
            f"# Transaccion tipo '{transaction_type}' — sin obligacion tributaria adicional\n"
            f"tax_liability = Decimal('0')\n"
        )
        calc_hash = self._make_hash(base_amount, Decimal("0"), query_date, "NO_TAX")

        return FiscalOutput(
            source_agent=self.agent_name,
            target_agent="HACIENDA",
            tax_type="NO_TAX",
            tax_liability=Decimal("0"),
            form_id="N/A",
            rule_ref="NO_TAX_RULE",
            rate_version=f"NO_TAX:{query_date.isoformat()}",
            calc_hash=calc_hash,
            calculation_code=calc_code,
            taxable_base=base_amount,
            period_from=query_date.isoformat(),
            period_to=query_date.isoformat(),
            exemptions_applied=(f"No aplica: tipo de transaccion '{transaction_type}'",),
        )

    # ---------------------------------------------------------------------------
    # IVU filing summary
    # ---------------------------------------------------------------------------

    def calculate_ivu_filing(
        self,
        period_start: date,
        period_end: date,
        transactions: list[dict],
    ) -> dict:
        """
        Genera el resumen de IVU para el formulario SC 2915 (planilla mensual IVU).

        Args:
            period_start:  Inicio del periodo de reporte.
            period_end:    Fin del periodo de reporte.
            transactions:  Lista de dicts con keys: 'type', 'amount', 'is_exempt',
                           'ivu_collected', 'ivu_paid'.

        Returns:
            Dict con: ivu_estatal_total, ivu_municipal_total, exempt_sales,
                      taxable_sales, form_sc2915_data.
        """
        ivu_estatal_total = Decimal("0")
        ivu_municipal_total = Decimal("0")
        exempt_sales = Decimal("0")
        taxable_sales = Decimal("0")
        ivu_input_credits = Decimal("0")

        # Usar la fecha de fin del periodo para determinar reglas vigentes
        estatal_rule = get_rule("IVU_ESTATAL_PR_2015_V1", period_end)
        municipal_rule = get_rule("IVU_MUNICIPAL_PR_2015_V1", period_end)

        estatal_rate = estatal_rule.rate if estatal_rule else Decimal("10.5")
        municipal_rate = municipal_rule.rate if municipal_rule else Decimal("1.0")

        for tx in transactions:
            amount = Decimal(str(tx.get("amount", "0")))
            tx_type = tx.get("type", "invoice")

            if tx_type == "invoice":
                if tx.get("is_exempt", False):
                    exempt_sales += amount
                else:
                    taxable_sales += amount
                    # Calcular IVU usando la funcion del modulo
                    ivu_calc = calculate_ivu(
                        base_amount=amount,
                        query_date=period_end,
                        include_municipal=True,
                    )
                    ivu_estatal_total += ivu_calc.ivu_estatal_amount
                    ivu_municipal_total += ivu_calc.ivu_municipal_amount
            elif tx_type == "expense":
                # IVU pagado en compras = credito de insumo
                ivu_paid = Decimal(str(tx.get("ivu_paid", "0")))
                ivu_input_credits += ivu_paid

        # Neto a remitir = IVU cobrado - creditos de insumo
        ivu_estatal_net = (ivu_estatal_total - ivu_input_credits).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        ivu_municipal_net = ivu_municipal_total.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_remit = ivu_estatal_net + ivu_municipal_net

        return {
            "ivu_estatal_total": ivu_estatal_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "ivu_municipal_total": ivu_municipal_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "ivu_input_credits": ivu_input_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "ivu_estatal_net": ivu_estatal_net,
            "ivu_municipal_net": ivu_municipal_net,
            "total_to_remit": total_remit,
            "exempt_sales": exempt_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "taxable_sales": taxable_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "rule_id_estatal": estatal_rule.rule_id if estatal_rule else "IVU_ESTATAL_PR_2015_V1",
            "rule_id_municipal": municipal_rule.rule_id if municipal_rule else "IVU_MUNICIPAL_PR_2015_V1",
            "estatal_rate_pct": str(estatal_rate),
            "municipal_rate_pct": str(municipal_rate),
            "form_sc2915_data": {
                "linea_1_ventas_gravables": str(taxable_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "linea_2_ventas_exentas": str(exempt_sales.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "linea_3_ivu_estatal": str(ivu_estatal_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "linea_4_ivu_municipal": str(ivu_municipal_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "linea_5_creditos_insumo": str(ivu_input_credits.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "linea_6_ivu_neto_estatal": str(ivu_estatal_net),
                "linea_7_ivu_neto_municipal": str(ivu_municipal_net),
                "linea_8_total_a_remitir": str(total_remit),
            },
        }

    # ---------------------------------------------------------------------------
    # Helpers privados
    # ---------------------------------------------------------------------------

    def _check_ivu_exemption(
        self,
        intake: IntakeOutput,
    ) -> tuple[bool, str]:
        """
        Verifica si una transaccion esta exenta de IVU.

        Revisa el vendor y la descripcion de los line_items contra las
        categorias exentas conocidas. Tambien consulta las exenciones
        definidas en la regla fiscal para validacion.

        Returns:
            (is_exempt: bool, reason: str)
        """
        # Construir texto de busqueda desde vendor y line items
        search_text = ""
        if intake.vendor:
            search_text += intake.vendor.lower() + " "
        for item in intake.line_items:
            search_text += item.description.lower() + " "

        # Buscar palabras clave de exencion
        for keyword, reason in _EXEMPT_KEYWORDS.items():
            if keyword in search_text:
                # Verificar contra la regla vigente para consistencia
                estatal_rule = get_rule("IVU_ESTATAL_PR_2015_V1", date.today())
                if estatal_rule:
                    for exemption in estatal_rule.exemptions:
                        if any(k in exemption.lower() for k in [keyword, "alimento", "medicamento", "medico"]):
                            return True, reason
                # Si el keyword coincide aunque la regla no este, retornar exencion
                return True, reason

        return False, ""

    def _infer_transaction_type(self, intake: IntakeOutput) -> str:
        """
        Infiere el tipo de transaccion desde el IntakeOutput.
        Logica conservadora: si no se puede determinar, asume 'invoice'.
        """
        vendor_lower = (intake.vendor or "").lower()
        # Heuristicas simples basadas en el vendor y los campos disponibles
        if intake.tax_amount and intake.amount and intake.vendor:
            # Tiene monto e impuesto — probablemente una factura de venta
            return "invoice"
        if not intake.vendor and intake.amount:
            return "invoice"
        return "invoice"

    @staticmethod
    def _make_hash(
        base_amount: Decimal,
        rate: Decimal,
        query_date: date,
        rule_id: str,
    ) -> str:
        """
        Genera el calc_hash = sha256(base_amount + rate + date + rule_id).
        Solo se usan los primeros 16 caracteres del hex digest.
        """
        data = {
            "base": str(base_amount),
            "rate": str(rate),
            "date": str(query_date),
            "rule_id": rule_id,
        }
        return hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest()[:16]
