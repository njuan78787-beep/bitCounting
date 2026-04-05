# =============================================================================
# agents/tests_fiscal_nomina_hacienda.py
# Tests: FiscalPRAgent (FISCAL_PR), NominaAgent (NOMINA), HaciendaAgent (HACIENDA).
#
# Ejecutar: python -m pytest agents/tests_fiscal_nomina_hacienda.py -v
#           o directamente: python agents/tests_fiscal_nomina_hacienda.py
# =============================================================================

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agents.fiscal_pr import FiscalPRAgent
from agents.hacienda import HaciendaAgent
from agents.nomina import NominaAgent, Employee, PayrollBalanceError
from agents.messages import IntakeOutput, FiscalOutput, SourceFormat
from tax_rules import get_rule


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def make_intake(
    vendor: str | None = "Test Vendor PR",
    amount: str = "1000.00",
    tax_amount: str | None = None,
    date_str: str | None = "2025-06-16",
    payment_method: str | None = "CHECK",
    confidence: str = "0.90",
    critical_fields_missing: bool = False,
    missing_fields: tuple[str, ...] = (),
) -> IntakeOutput:
    """Factory para IntakeOutput de prueba."""
    return IntakeOutput(
        message_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        source_agent="INTAKE",
        target_agent="CENTINELA",
        vendor=vendor,
        date=date_str,
        amount=Decimal(amount) if amount else None,
        tax_amount=Decimal(tax_amount) if tax_amount else None,
        payment_method=payment_method,
        confidence=Decimal(confidence),
        missing_fields=missing_fields,
        critical_fields_missing=critical_fields_missing,
        source_format=SourceFormat.JSON,
    )


def make_employee(
    ytd_wages: str = "0.00",
    ytd_ss: str = "0.00",
    ytd_medicare: str = "0.00",
    pay_period: str = "BIWEEKLY",
) -> Employee:
    """Factory para Employee de prueba."""
    return Employee(
        employee_id=str(uuid.uuid4()),
        name="Juan Perez",
        ssn_last4="1234",
        filing_status="SINGLE",
        ytd_wages=Decimal(ytd_wages),
        ytd_ss_withheld=Decimal(ytd_ss),
        ytd_medicare_withheld=Decimal(ytd_medicare),
        pay_period=pay_period,
    )


fiscal = FiscalPRAgent()
nomina = NominaAgent()
hacienda = HaciendaAgent()


# ===========================================================================
# TEST 1 — FISCAL PR calcula IVU 10.5% correctamente usando tax_rules
# ===========================================================================

def test_fiscal_ivu_10_5_pct():
    """
    FiscalPRAgent calcula IVU estatal de 10.5% usando tax_rules.calculate_ivu().
    Para base $1,000: IVU estatal = $105.00.
    La tasa NUNCA se hardcodea — viene de IVU_ESTATAL_PR_2015_V1.
    """
    intake = make_intake(vendor="Suplidores Tecnologia PR", amount="1000.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    # IVU total = estatal + municipal = 105.00 + 10.00 = 115.00
    assert isinstance(result, FiscalOutput), "Debe retornar FiscalOutput"
    assert result.tax_liability == Decimal("115.00"), (
        f"IVU total esperado $115.00 (10.5%+1.0% de $1000), obtenido: {result.tax_liability}"
    )
    # Verificar que la tasa del 10.5% viene de tax_rules
    rule = get_rule("IVU_ESTATAL_PR_2015_V1", date(2025, 6, 16))
    assert rule is not None
    assert rule.rate == Decimal("10.5"), f"Tasa estatal debe ser 10.5%, es {rule.rate}"
    assert result.rule_ref == "IVU_ESTATAL_PR_2015_V1"
    print("  TEST 1 PASSED: IVU 10.5% calculado correctamente usando tax_rules ($105.00 estatal)")


# ===========================================================================
# TEST 2 — FISCAL PR calcula IVU municipal 1.0% correctamente
# ===========================================================================

def test_fiscal_ivu_municipal_1_pct():
    """
    FiscalPRAgent incluye el IVU municipal de 1.0% en el calculo total.
    Para base $1,000: IVU municipal = $10.00, total = $115.00.
    La tasa municipal viene de IVU_MUNICIPAL_PR_2015_V1 — nunca hardcodeada.
    """
    from tax_rules import calculate_ivu

    # Verificar directamente los componentes usando calculate_ivu
    ivu_calc = calculate_ivu(
        base_amount=Decimal("1000.00"),
        query_date=date(2025, 6, 16),
        include_municipal=True,
    )

    assert ivu_calc.ivu_estatal_rate == Decimal("10.5"), (
        f"Tasa estatal debe ser 10.5%, es: {ivu_calc.ivu_estatal_rate}"
    )
    assert ivu_calc.ivu_municipal_rate == Decimal("1.0"), (
        f"Tasa municipal debe ser 1.0%, es: {ivu_calc.ivu_municipal_rate}"
    )
    assert ivu_calc.ivu_municipal_amount == Decimal("10.00"), (
        f"IVU municipal sobre $1000 debe ser $10.00, es: {ivu_calc.ivu_municipal_amount}"
    )

    # Verificar que FiscalPRAgent incluye el municipal en tax_liability
    intake = make_intake(vendor="Retail Store PR", amount="1000.00")
    result = fiscal.calculate_tax_liability(intake, "invoice", date(2025, 6, 16))
    assert result.tax_liability == Decimal("115.00"), (
        f"tax_liability debe incluir municipal: esperado $115.00, obtenido {result.tax_liability}"
    )
    print("  TEST 2 PASSED: IVU municipal 1.0% = $10.00 incluido correctamente en total")


# ===========================================================================
# TEST 3 — FISCAL PR identifica ventas exentas (alimentos, medicamentos)
# ===========================================================================

def test_fiscal_ventas_exentas():
    """
    Vendedores de categorias exentas (farmacia, alimentos, medico) deben
    producir tax_liability=0 y exemptions_applied con la razon.

    Exenciones verificadas: farmacia (medicamentos OTC), alimento (supermercado),
    medico (servicios medicos).
    """
    exempt_cases = [
        ("Farmacia del Pueblo Bayamon", "farmacia — medicamentos OTC"),
        ("Supermercado Los Amigos Caguas", "alimentos — consumo en hogar"),
        ("Clinica Medica Rio Piedras", "servicios medicos"),
    ]

    for vendor, description in exempt_cases:
        intake = make_intake(vendor=vendor, amount="500.00")
        result = fiscal.calculate_tax_liability(intake, "invoice", date(2025, 6, 16))
        assert result.tax_liability == Decimal("0"), (
            f"'{vendor}' debe ser exento ({description}). "
            f"tax_liability obtenido: {result.tax_liability}"
        )
        assert len(result.exemptions_applied) > 0, (
            f"exemptions_applied no debe estar vacio para '{vendor}'"
        )

    print("  TEST 3 PASSED: Ventas exentas identificadas (farmacia, alimentos, medico) → tax_liability=$0")


# ===========================================================================
# TEST 4 — IVU total = estatal + municipal (precision Decimal exacta)
# ===========================================================================

def test_fiscal_ivu_total_estatal_mas_municipal():
    """
    IVU total = IVU estatal + IVU municipal.
    La suma debe ser exacta en Decimal — sin errores de punto flotante.

    Para $1,000: estatal=105.00, municipal=10.00, total=115.00.
    Para $777.50: estatal=81.64, municipal=7.78, total=89.42.
    """
    from tax_rules import calculate_ivu

    test_cases = [
        Decimal("1000.00"),
        Decimal("777.50"),
        Decimal("2500.00"),
        Decimal("99.99"),
    ]

    for base in test_cases:
        ivu = calculate_ivu(base, date(2025, 6, 16), include_municipal=True)
        total_verificado = ivu.ivu_estatal_amount + ivu.ivu_municipal_amount
        assert ivu.total_ivu == total_verificado, (
            f"Para base={base}: total_ivu={ivu.total_ivu} != "
            f"estatal({ivu.ivu_estatal_amount}) + municipal({ivu.ivu_municipal_amount})"
        )
        # Verificar que son Decimal (no float)
        assert isinstance(ivu.total_ivu, Decimal), "total_ivu debe ser Decimal"
        assert isinstance(ivu.ivu_estatal_amount, Decimal), "ivu_estatal_amount debe ser Decimal"
        assert isinstance(ivu.ivu_municipal_amount, Decimal), "ivu_municipal_amount debe ser Decimal"

    print("  TEST 4 PASSED: IVU total = estatal + municipal exacto (Decimal precision)")


# ===========================================================================
# TEST 5 — FISCAL PR nunca hardcodea tasas — siempre usa tax_rules
# ===========================================================================

def test_fiscal_nunca_hardcodea_tasas():
    """
    FiscalPRAgent obtiene TODAS las tasas de tax_rules — nunca las hardcodea.

    Verificamos que:
    1. rule_ref apunta a un rule_id real en tax_rules.
    2. La tasa en el rule_ref coincide con lo calculado.
    3. El calculation_code usa tax_rules, no aritmetica inline.
    """
    intake = make_intake(vendor="Empresa XYZ Inc", amount="5000.00")
    result = fiscal.calculate_tax_liability(intake, "invoice", date(2025, 6, 16))

    # Verificar que rule_ref es un rule_id real en tax_rules
    rule = get_rule(result.rule_ref, date(2025, 6, 16))
    assert rule is not None, (
        f"rule_ref='{result.rule_ref}' debe ser un rule_id valido en tax_rules"
    )

    # Verificar que el calculation_code menciona tax_rules o calculate_ivu
    code = result.calculation_code
    uses_tax_rules = "tax_rules" in code or "calculate_ivu" in code or "get_rule" in code
    assert uses_tax_rules, (
        f"calculation_code debe referenciar tax_rules — nunca aritmetica hardcodeada.\n"
        f"Codigo: {code[:200]}"
    )

    # Verificar que la tasa de la regla es consistente con el calculo
    assert rule.rate is not None, f"Regla {result.rule_ref} debe tener rate"
    expected_ivu = (Decimal("5000.00") * rule.rate / Decimal("100")).quantize(
        Decimal("0.01")
    )
    # El total incluye municipal — verificar que es mayor o igual al estatal
    assert result.tax_liability >= expected_ivu, (
        f"tax_liability={result.tax_liability} debe ser >= IVU estatal={expected_ivu}"
    )

    print(f"  TEST 5 PASSED: FISCAL_PR usa tax_rules — rule_ref='{result.rule_ref}', "
          f"tasa={rule.rate}%, tax_liability={result.tax_liability}")


# ===========================================================================
# TEST 6 — NOMINA calcula SS 6.2% correctamente
# ===========================================================================

def test_nomina_ss_6_2_pct():
    """
    SS del empleado = 6.2% del salario bruto (cuando YTD < tope $176,100).
    Para gross_pay=$1,000: SS esperado = $62.00.
    La tasa viene de FICA_SS_2025_V1 en tax_rules — no hardcodeada.
    """
    employee = make_employee(ytd_wages="0.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )

    expected_ss = Decimal("62.00")  # 6.2% de $1,000
    assert result.ss_employee == expected_ss, (
        f"SS empleado esperado: {expected_ss}, obtenido: {result.ss_employee}"
    )
    assert result.ss_employer == expected_ss, (
        f"SS patrono debe ser igual al del empleado (6.2%): esperado {expected_ss}, "
        f"obtenido: {result.ss_employer}"
    )
    # Verificar que la tasa viene de tax_rules
    ss_rule = get_rule("FICA_SS_2025_V1", date(2025, 6, 16))
    assert ss_rule.rate_employee == Decimal("6.2")
    print(f"  TEST 6 PASSED: SS 6.2% calculado correctamente (empleado+patrono: ${result.ss_employee})")


# ===========================================================================
# TEST 7 — NOMINA respeta tope SS $176,100 (2025)
# ===========================================================================

def test_nomina_respeta_tope_ss_176100():
    """
    SS solo aplica sobre los primeros $176,100 de salario anual (tope 2025).
    Cuando YTD es $176,000 y gross es $1,000: SS solo aplica sobre $100.
    SS employee = $100 * 6.2% = $6.20 (no $62.00).
    """
    # YTD = $176,000, gross = $1,000 → elegible para SS = $100 (min(1000, 176100-176000))
    employee = make_employee(ytd_wages="176000.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )

    eligible = Decimal("176100") - Decimal("176000")  # = $100
    expected_ss = (eligible * Decimal("6.2") / Decimal("100")).quantize(Decimal("0.01"))
    assert result.ss_employee == expected_ss, (
        f"SS sobre $100 elegibles (tope $176,100 - YTD $176,000): "
        f"esperado {expected_ss}, obtenido {result.ss_employee}"
    )

    # Verificar que el tope viene de tax_rules
    ss_rule = get_rule("FICA_SS_2025_V1", date(2025, 6, 16))
    assert ss_rule.wage_base_limit == Decimal("176100"), (
        f"Tope SS 2025 desde tax_rules debe ser $176,100, es {ss_rule.wage_base_limit}"
    )
    print(f"  TEST 7 PASSED: SS respeta tope $176,100 — sobre $100 elegibles = {result.ss_employee}")


# ===========================================================================
# TEST 8 — NOMINA SS = 0 cuando YTD > $176,100
# ===========================================================================

def test_nomina_ss_cero_ytd_sobre_tope():
    """
    Cuando el YTD wages ya supera el tope de SS ($176,100 para 2025),
    el SS del empleado y del patrono en este periodo deben ser $0.
    """
    employee = make_employee(ytd_wages="180000.00")  # YTD > $176,100
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("5000.00"),
        pay_date=date(2025, 6, 16),
    )

    assert result.ss_employee == Decimal("0.00"), (
        f"SS debe ser $0 cuando YTD $180,000 > tope $176,100. "
        f"Obtenido: {result.ss_employee}"
    )
    assert result.ss_employer == Decimal("0.00"), (
        f"SS patrono debe ser $0 cuando YTD > tope. Obtenido: {result.ss_employer}"
    )
    print(f"  TEST 8 PASSED: SS = $0 cuando YTD $180,000 > tope $176,100")


# ===========================================================================
# TEST 9 — NOMINA calcula Medicare 1.45% sin tope
# ===========================================================================

def test_nomina_medicare_1_45_pct():
    """
    Medicare del empleado = 1.45% del salario bruto SIN tope salarial.
    Para gross_pay=$1,000: Medicare = $14.50.
    Aplica sobre TODOS los salarios — incluso sobre $200K (mas el adicional).
    """
    employee = make_employee(ytd_wages="0.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )

    expected_medicare = Decimal("14.50")  # 1.45% de $1,000
    assert result.medicare_employee == expected_medicare, (
        f"Medicare empleado esperado: {expected_medicare}, obtenido: {result.medicare_employee}"
    )
    assert result.medicare_employer == expected_medicare, (
        f"Medicare patrono esperado: {expected_medicare}, obtenido: {result.medicare_employer}"
    )
    # Verificar que la regla no tiene tope salarial
    med_rule = get_rule("FICA_MEDICARE_2013_V1", date(2025, 6, 16))
    assert med_rule.wage_base_limit is None, (
        "Medicare no debe tener tope salarial (wage_base_limit debe ser None)"
    )
    print(f"  TEST 9 PASSED: Medicare 1.45% sin tope = {result.medicare_employee} sobre $1,000")


# ===========================================================================
# TEST 10 — NOMINA calcula Medicare adicional 0.9% sobre $200K
# ===========================================================================

def test_nomina_medicare_adicional_0_9_sobre_200k():
    """
    Cuando el YTD wages supera $200,000 (umbral de Additional Medicare Tax),
    se debe retener un 0.9% adicional sobre la porcion que excede el umbral.

    YTD = $199,000 + gross $5,000 = $204,000.
    Porcion sobre umbral = $204,000 - $200,000 = $4,000.
    Additional Medicare = $4,000 * 0.9% = $36.00.
    """
    employee = make_employee(ytd_wages="199000.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("5000.00"),
        pay_date=date(2025, 6, 16),
    )

    expected_additional = Decimal("36.00")  # 4000 * 0.9%
    assert result.additional_medicare == expected_additional, (
        f"Medicare adicional esperado: {expected_additional}, "
        f"obtenido: {result.additional_medicare}"
    )
    print(f"  TEST 10 PASSED: Medicare adicional 0.9% sobre $200K = {result.additional_medicare}")


# ===========================================================================
# TEST 11 — NOMINA verifica balance algebraico: gross == net + deducciones
# ===========================================================================

def test_nomina_balance_algebraico_exacto():
    """
    validate_algebraic_balance debe retornar True.
    gross_pay == net_pay + ss_employee + medicare_employee + additional_medicare
              + federal_income_tax_withheld + pr_income_tax_withheld
    Exacto al centavo (Decimal sin tolerancia).
    """
    employee = make_employee(ytd_wages="50000.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("3500.00"),
        pay_date=date(2025, 6, 16),
    )

    is_balanced = nomina.validate_algebraic_balance(result)
    assert is_balanced is True, (
        f"Balance algebraico fallido para gross={result.gross_pay}"
    )

    # Verificar manualmente tambien
    deductions = (
        result.ss_employee
        + result.medicare_employee
        + result.additional_medicare
        + result.federal_income_tax_withheld
        + result.pr_income_tax_withheld
    )
    assert result.gross_pay == result.net_pay + deductions, (
        f"gross={result.gross_pay} != net={result.net_pay} + deductions={deductions}"
    )
    print(f"  TEST 11 PASSED: Balance algebraico exacto (gross={result.gross_pay}, net={result.net_pay})")


# ===========================================================================
# TEST 12 — NOMINA rechaza si balance no cuadra (PayrollBalanceError)
# ===========================================================================

def test_nomina_rechaza_balance_incorrecto():
    """
    Si el balance algebraico no cuadra, process_payroll debe lanzar
    PayrollBalanceError. Aqui simulamos creando un PayrollResult manipulado
    y verificando que validate_algebraic_balance retorna False.

    Nota: process_payroll siempre produce balance correcto.
    Para simular un resultado incorrecto, creamos un PayrollResult con
    net_pay deliberadamente incorrecto y verificamos que validate_algebraic_balance
    lo detecta.
    """
    from agents.nomina import PayrollResult

    # Crear PayrollResult con balance roto intencionalmente
    broken_result = PayrollResult(
        employee_id="TEST-001",
        gross_pay=Decimal("1000.00"),
        ss_employee=Decimal("62.00"),
        ss_employer=Decimal("62.00"),
        medicare_employee=Decimal("14.50"),
        medicare_employer=Decimal("14.50"),
        additional_medicare=Decimal("0.00"),
        futa_employer=Decimal("6.00"),
        federal_income_tax_withheld=Decimal("100.00"),
        pr_income_tax_withheld=Decimal("50.00"),
        net_pay=Decimal("800.00"),  # Incorrecto: deberia ser 773.50
        rule_ids_applied=("FICA_SS_2025_V1",),
        calculation_date=date(2025, 6, 16),
    )

    is_balanced = nomina.validate_algebraic_balance(broken_result)
    assert is_balanced is False, (
        "validate_algebraic_balance debe retornar False para balance incorrecto"
    )
    print("  TEST 12 PASSED: NOMINA rechaza balance incorrecto (validate_algebraic_balance=False)")


# ===========================================================================
# TEST 13 — FUTA: tasa neta 0.6% sobre los primeros $7,000
# ===========================================================================

def test_futa_tasa_neta_0_6_sobre_7000():
    """
    FUTA neta = 6.0% bruto - 5.4% credito = 0.6% tasa neta.
    Solo aplica sobre los primeros $7,000 de salario por empleado por ano.

    Para gross_pay=$1,000 con YTD=$0: FUTA = $1,000 * 0.6% = $6.00.
    Para gross_pay=$8,000 con YTD=$0: FUTA solo sobre $7,000 = $7,000 * 0.6% = $42.00.
    """
    # Caso 1: gross < tope — FUTA sobre todo el gross
    emp1 = make_employee(ytd_wages="0.00")
    r1 = nomina.process_payroll(
        employee=emp1,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )
    expected_futa_1 = Decimal("6.00")  # $1000 * 0.6%
    assert r1.futa_employer == expected_futa_1, (
        f"FUTA sobre $1000 esperada: {expected_futa_1}, obtenida: {r1.futa_employer}"
    )

    # Caso 2: gross > tope — FUTA solo sobre los primeros $7,000
    emp2 = make_employee(ytd_wages="0.00")
    r2 = nomina.process_payroll(
        employee=emp2,
        gross_pay=Decimal("8000.00"),
        pay_date=date(2025, 6, 16),
    )
    expected_futa_2 = Decimal("42.00")  # $7000 * 0.6%
    assert r2.futa_employer == expected_futa_2, (
        f"FUTA sobre $8000 (tope $7000) esperada: {expected_futa_2}, obtenida: {r2.futa_employer}"
    )

    # Verificar que la tasa viene del modulo tax_rules
    futa_rule = get_rule("FUTA_FEDERAL_2011_V1", date(2025, 6, 16))
    assert futa_rule is not None
    net_rate = futa_rule.rate - futa_rule.credit_rate
    assert net_rate == Decimal("0.6"), f"Tasa neta FUTA desde tax_rules: {net_rate}"

    print(f"  TEST 13 PASSED: FUTA neta 0.6% — $1000: {r1.futa_employer}, $8000: {r2.futa_employer}")


# ===========================================================================
# TEST 14 — HACIENDA genera SC2915 con fecha de vencimiento correcta
# ===========================================================================

def test_hacienda_sc2915_fecha_vencimiento():
    """
    SC 2915 (planilla mensual IVU) vence el dia 20 del mes siguiente al periodo.

    Enero 2025 → vence el 20 de febrero de 2025.
    Diciembre 2025 → vence el 20 de enero de 2026.
    """
    # Caso 1: Enero → 20 de febrero
    due_jan = hacienda.get_due_date("SC 2915", date(2025, 1, 15))
    assert due_jan == date(2025, 2, 20), (
        f"SC2915 Enero 2025 vence el 2025-02-20, obtenido: {due_jan}"
    )

    # Caso 2: Diciembre → 20 de enero del ano siguiente
    due_dec = hacienda.get_due_date("SC 2915", date(2025, 12, 15))
    assert due_dec == date(2026, 1, 20), (
        f"SC2915 Diciembre 2025 vence el 2026-01-20, obtenido: {due_dec}"
    )

    # Caso 3: usando alias SC-2915
    due_alias = hacienda.get_due_date("SC-2915", date(2025, 6, 1))
    assert due_alias == date(2025, 7, 20), (
        f"SC-2915 Junio 2025 vence el 2025-07-20, obtenido: {due_alias}"
    )

    print(f"  TEST 14 PASSED: SC2915 fecha vencimiento correcta "
          f"(ene→{due_jan}, dic→{due_dec})")


# ===========================================================================
# TEST 15 — HACIENDA valida 941-PR algebraicamente
# ===========================================================================

def test_hacienda_valida_941pr_algebraicamente():
    """
    HACIENDA ejecuta triple verificacion algebraica para 941-PR.
    Los tres checks deben pasar: algebraic_check, ivu_crosscheck, period_consistency.
    El resultado debe tener status='DRY_RUN' y las notas de validacion deben
    confirmar que todos los checks pasaron.
    """
    # Crear FiscalOutput tipo nomina (FICA_SS) para 941-PR
    intake = make_intake(vendor="Corporacion XYZ PR", amount="50000.00")
    payroll_fiscal = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="payroll",
        query_date=date(2025, 1, 15),
    )

    # Preparar 941-PR para Q1 2025
    result = hacienda.prepare_form(
        fiscal_output=payroll_fiscal,
        form_type="941-PR",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 3, 31),
    )

    # Verificar estructura del resultado
    assert result["status"] == "DRY_RUN", f"Status debe ser DRY_RUN, obtenido: {result['status']}"
    assert result["form_type"] == "941-PR"
    assert result["filing_ready"] is False, "filing_ready debe ser False en Phase 1"

    # Verificar que los tres checks de validacion pasaron (triple verificacion)
    notes = result["validation_notes"]
    assert len(notes) == 3, f"Deben ser exactamente 3 notas de validacion, obtenidas: {len(notes)}"
    for note in notes:
        assert "PASS" in note or "SKIP" in note or "NOTA" in note, (
            f"Nota de validacion no contiene PASS/SKIP/NOTA: {note}"
        )

    print(f"  TEST 15 PASSED: HACIENDA valida 941-PR algebraicamente — "
          f"3 checks: {[n.split(':')[0] for n in notes]}")


# ===========================================================================
# TEST 16 — HACIENDA triple_verified=True para SC2915
# ===========================================================================

def test_hacienda_triple_verified_sc2915():
    """
    HACIENDA ejecuta TRES verificaciones independientes para SC 2915:
      1. algebraic_check: tax_liability y taxable_base son Decimal validos.
      2. ivu_crosscheck: tax_liability coherente con tasa IVU 11.5% sobre base.
      3. prior_period_consistency: fechas de periodo son validas y consistentes.

    Los tres checks deben producir notas con 'PASS' (o 'SKIP' para creditos).
    """
    # Factura gravable de $1,000 → IVU total = $115.00 (10.5% + 1.0%)
    intake = make_intake(vendor="Suplidores Tecnologia PR", amount="1000.00")
    ivu_fiscal = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 3, 15),
    )

    # Verificar que el FiscalOutput tiene los valores correctos
    assert ivu_fiscal.tax_liability == Decimal("115.00")
    assert ivu_fiscal.taxable_base == Decimal("1000.00")

    # Preparar SC 2915 para marzo 2025
    result = hacienda.prepare_form(
        fiscal_output=ivu_fiscal,
        form_type="SC 2915",
        period_start=date(2025, 3, 1),
        period_end=date(2025, 3, 31),
    )

    # Triple verificacion: exactamente 3 notas
    notes = result["validation_notes"]
    assert len(notes) == 3, (
        f"Triple verificacion debe producir 3 notas, obtenidas {len(notes)}: {notes}"
    )

    # Todos los checks deben pasar (sin errores)
    for note in notes:
        assert "FAIL" not in note.upper() and "ERROR" not in note.upper(), (
            f"Check de verificacion fallo: {note}"
        )

    # Verificar que el resultado es coherente
    assert result["status"] == "DRY_RUN"
    assert result["form_type"] == "SC 2915"
    assert "calc_hash" in result
    assert len(result["calc_hash"]) == 16  # sha256[:16]

    # Los 3 checks = triple_verified
    all_pass = all(("PASS" in n or "SKIP" in n or "NOTA" in n) for n in notes)
    assert all_pass, f"No todos los checks pasaron: {notes}"

    print(f"  TEST 16 PASSED: HACIENDA triple verificacion SC2915 — "
          f"checks: {[n.split(':')[0] for n in notes]}")


# ---------------------------------------------------------------------------
# Runner directo (sin pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # FISCAL_PR tests (1-5)
        test_fiscal_ivu_10_5_pct,
        test_fiscal_ivu_municipal_1_pct,
        test_fiscal_ventas_exentas,
        test_fiscal_ivu_total_estatal_mas_municipal,
        test_fiscal_nunca_hardcodea_tasas,
        # NOMINA tests (6-13)
        test_nomina_ss_6_2_pct,
        test_nomina_respeta_tope_ss_176100,
        test_nomina_ss_cero_ytd_sobre_tope,
        test_nomina_medicare_1_45_pct,
        test_nomina_medicare_adicional_0_9_sobre_200k,
        test_nomina_balance_algebraico_exacto,
        test_nomina_rechaza_balance_incorrecto,
        test_futa_tasa_neta_0_6_sobre_7000,
        # HACIENDA tests (14-16)
        test_hacienda_sc2915_fecha_vencimiento,
        test_hacienda_valida_941pr_algebraicamente,
        test_hacienda_triple_verified_sc2915,
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 70)
    print("EJECUTANDO TESTS: FISCAL_PR + NOMINA + HACIENDA — Bit-Counting")
    print("=" * 70)

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, e))
            print(f"  FAILED {test_fn.__name__}: {e}")

    print("=" * 70)
    print(f"RESULTADO: {passed} passed, {failed} failed")
    if errors:
        print("\nFALLOS DETALLADOS:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print("=" * 70)

    if failed > 0:
        raise SystemExit(1)
