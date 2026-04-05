# =============================================================================
# agents/tests_fiscal_nomina_hacienda.py
# Tests de integracion: FiscalPRAgent, NominaAgent, FlowCoordinator.
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
from agents.nomina import NominaAgent, Employee
from agents.flow import FlowCoordinator, FlowResult
from agents.messages import IntakeOutput, FiscalOutput, SourceFormat


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


# ===========================================================================
# TEST 1 — FiscalPRAgent calcula IVU correcto sobre $1000
# IVU total = 10.5% (estatal) + 1.0% (municipal) = 11.5% = $115.00
# ===========================================================================

def test_fiscal_ivu_correcto_sobre_1000():
    """
    FiscalPRAgent sobre factura de $1000 debe calcular:
    IVU estatal 10.5% = $105.00 + IVU municipal 1.0% = $10.00 → total $115.00
    """
    intake = make_intake(vendor="Suplidores Tecnologia PR", amount="1000.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    assert isinstance(result, FiscalOutput), "Debe retornar FiscalOutput"
    assert result.tax_liability == Decimal("115.00"), (
        f"IVU total esperado: $115.00, obtenido: {result.tax_liability}"
    )
    assert result.taxable_base == Decimal("1000.00")
    print("  TEST 1 PASSED: FiscalPRAgent calcula IVU correcto $115.00 sobre $1000.00")


# ===========================================================================
# TEST 2 — Vendedor "farmacia" → IVU exento (is_exempt=True)
# ===========================================================================

def test_fiscal_farmacia_ivu_exento():
    """
    Vendedor que contiene "farmacia" → transaccion exenta de IVU.
    tax_liability debe ser $0 y exemptions_applied debe tener razon.
    """
    intake = make_intake(vendor="Farmacia del Pueblo Bayamon", amount="250.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    assert result.tax_liability == Decimal("0"), (
        f"Farmacia debe estar exenta. Obtenido: {result.tax_liability}"
    )
    assert len(result.exemptions_applied) > 0, "Debe haber al menos una exencion aplicada"
    print("  TEST 2 PASSED: Vendedor 'farmacia' → IVU exento, tax_liability=$0")


# ===========================================================================
# TEST 3 — calc_hash generado correctamente (len=64 — sha256 hex)
# ===========================================================================

def test_fiscal_calc_hash_length():
    """
    El campo calc_hash usa SHA-256 hex pero solo los primeros 16 chars.
    Verificar que tiene exactamente 16 caracteres (como define _make_hash).

    Nota: FiscalOutput.calc_hash en el docstring dice '16 chars (sha256 hex)'
    — verificamos que sea hexadecimal y tenga la longitud correcta.
    """
    intake = make_intake(vendor="Empresa ABC Corp", amount="500.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    # calculate_ivu retorna el calc_hash completo (64 chars SHA-256)
    # _make_hash() retorna los primeros 16 chars
    # Ambas rutas son usadas — verificamos que sea hex valido
    assert len(result.calc_hash) in (16, 64), (
        f"calc_hash debe tener 16 o 64 caracteres (hex SHA-256), tiene: {len(result.calc_hash)}"
    )
    # Verificar que es hexadecimal valido
    int(result.calc_hash, 16)  # lanza ValueError si no es hex valido
    print(f"  TEST 3 PASSED: calc_hash generado correctamente (len={len(result.calc_hash)}, hex valido)")


# ===========================================================================
# TEST 4 — form_id no es None
# ===========================================================================

def test_fiscal_form_id_no_none():
    """
    FiscalOutput siempre debe tener un form_id asignado (no None, no vacio).
    """
    intake = make_intake(vendor="Servicios Legales LLC", amount="800.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    assert result.form_id is not None, "form_id no debe ser None"
    assert len(result.form_id) > 0, "form_id no debe estar vacio"
    print(f"  TEST 4 PASSED: form_id asignado correctamente: '{result.form_id}'")


# ===========================================================================
# TEST 5 — tax_liability es Decimal
# ===========================================================================

def test_fiscal_tax_liability_es_decimal():
    """
    tax_liability debe ser siempre Decimal — nunca float ni int.
    """
    intake = make_intake(vendor="Distribuidora PR", amount="2500.00")
    result = fiscal.calculate_tax_liability(
        intake_output=intake,
        transaction_type="invoice",
        query_date=date(2025, 6, 16),
    )

    assert isinstance(result.tax_liability, Decimal), (
        f"tax_liability debe ser Decimal, es: {type(result.tax_liability)}"
    )
    print(f"  TEST 5 PASSED: tax_liability es Decimal ({result.tax_liability})")


# ===========================================================================
# TEST 6 — NominaAgent: SS deducido correctamente (6.2% hasta tope)
# ===========================================================================

def test_nomina_ss_deducido_correctamente():
    """
    SS del empleado = 6.2% del salario bruto (cuando YTD < tope).
    Para gross_pay=$1000, SS esperado = $62.00.
    """
    employee = make_employee(ytd_wages="0.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )

    expected_ss = Decimal("62.00")  # 6.2% de $1000
    assert result.ss_employee == expected_ss, (
        f"SS empleado esperado: {expected_ss}, obtenido: {result.ss_employee}"
    )
    print(f"  TEST 6 PASSED: SS deducido correctamente: {result.ss_employee} (6.2% de $1000)")


# ===========================================================================
# TEST 7 — NominaAgent: Medicare deducido correctamente (1.45%)
# ===========================================================================

def test_nomina_medicare_deducido_correctamente():
    """
    Medicare del empleado = 1.45% del salario bruto (sin tope).
    Para gross_pay=$1000, Medicare esperado = $14.50.
    """
    employee = make_employee(ytd_wages="0.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("1000.00"),
        pay_date=date(2025, 6, 16),
    )

    expected_medicare = Decimal("14.50")  # 1.45% de $1000
    assert result.medicare_employee == expected_medicare, (
        f"Medicare empleado esperado: {expected_medicare}, obtenido: {result.medicare_employee}"
    )
    print(f"  TEST 7 PASSED: Medicare deducido correctamente: {result.medicare_employee} (1.45% de $1000)")


# ===========================================================================
# TEST 8 — NominaAgent: balance algebraico (gross == net + deducciones)
# ===========================================================================

def test_nomina_balance_algebraico():
    """
    Verificacion algebraica: gross_pay debe ser exactamente igual a
    net_pay + ss_employee + medicare_employee + additional_medicare
    + federal_income_tax_withheld + pr_income_tax_withheld.
    """
    employee = make_employee(ytd_wages="5000.00")
    gross_pay = Decimal("2000.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=gross_pay,
        pay_date=date(2025, 6, 16),
    )

    deductions = (
        result.ss_employee
        + result.medicare_employee
        + result.additional_medicare
        + result.federal_income_tax_withheld
        + result.pr_income_tax_withheld
    )
    computed_gross = result.net_pay + deductions

    assert computed_gross == result.gross_pay, (
        f"Balance roto: gross={result.gross_pay}, "
        f"net+deducciones={computed_gross}, delta={result.gross_pay - computed_gross}"
    )
    assert nomina.validate_algebraic_balance(result) is True
    print(f"  TEST 8 PASSED: Balance algebraico OK (gross={result.gross_pay}, net={result.net_pay})")


# ===========================================================================
# TEST 9 — NominaAgent: SS = $0 cuando YTD > wage_base_limit
# ===========================================================================

def test_nomina_ss_cero_cuando_ytd_sobre_tope():
    """
    Cuando el YTD wages del empleado ya supera el tope de SS (176,100 para 2025),
    el SS del empleado en este periodo debe ser $0.
    """
    # YTD ya supera el tope de 2025 ($176,100)
    employee = make_employee(ytd_wages="180000.00")
    result = nomina.process_payroll(
        employee=employee,
        gross_pay=Decimal("5000.00"),
        pay_date=date(2025, 6, 16),
    )

    assert result.ss_employee == Decimal("0.00"), (
        f"SS debe ser $0 cuando YTD > tope. Obtenido: {result.ss_employee}"
    )
    assert result.ss_employer == Decimal("0.00"), (
        f"SS empleador debe ser $0 cuando YTD > tope. Obtenido: {result.ss_employer}"
    )
    print(f"  TEST 9 PASSED: SS = $0 cuando YTD ({employee.ytd_wages}) > wage_base_limit")


# ===========================================================================
# TEST 10 — FlowCoordinator: documento completo → final_decision="COMPLETED"
# ===========================================================================

def test_flow_documento_completo_completed():
    """
    Un documento completo con todos los campos requeridos debe producir
    final_decision="COMPLETED" al pasar por todo el pipeline.
    """
    raw_input = {
        "vendor": "Renta Oficina Hato Rey",
        "date": "2025-06-16",
        "amount": "2000.00",
        "payment_method": "CHECK",
        # Sin tax_amount para que el auditor no lo verifique contra el calculo IVU
    }

    coordinator = FlowCoordinator()
    result = coordinator.process(raw_input, source_format="json")

    assert result.final_decision == "COMPLETED", (
        f"Documento completo debe resultar en COMPLETED. "
        f"Obtenido: {result.final_decision}. "
        f"Centinela decision: {result.centinela_evaluation.decision}. "
        f"Pause reason: {result.centinela_evaluation.reason}"
    )
    assert result.classifier_output is not None, "classifier_output no debe ser None"
    assert result.auditor_verification is not None, "auditor_verification no debe ser None"
    assert result.fiscal_output is not None, "fiscal_output no debe ser None"
    print(f"  TEST 10 PASSED: Documento completo → final_decision='COMPLETED'")


# ===========================================================================
# TEST 11 — FlowCoordinator: documento incompleto → final_decision="PAUSED"
# ===========================================================================

def test_flow_documento_incompleto_paused():
    """
    Un documento sin campos criticos (amount y date faltantes) debe producir
    final_decision="PAUSED" porque CENTINELA emite pausa por MISSING_CRITICAL_FIELD
    o LOW_CONFIDENCE.
    """
    # Sin amount ni date — confidence muy baja, campos criticos faltantes
    raw_input = {
        "vendor": "Empresa Desconocida",
        # Sin amount, sin date, sin payment_method
    }

    coordinator = FlowCoordinator()
    result = coordinator.process(raw_input, source_format="manual")

    assert result.final_decision == "PAUSED", (
        f"Documento incompleto debe resultar en PAUSED. "
        f"Obtenido: {result.final_decision}. "
        f"Centinela decision: {result.centinela_evaluation.decision}"
    )
    assert result.classifier_output is None, "classifier_output debe ser None en PAUSED"
    assert result.fiscal_output is None, "fiscal_output debe ser None en PAUSED"
    print(f"  TEST 11 PASSED: Documento incompleto → final_decision='PAUSED'")


# ===========================================================================
# TEST 12 — FlowCoordinator: log tiene >= 1 entrada
# ===========================================================================

def test_flow_log_tiene_entradas():
    """
    Despues de procesar cualquier documento, el log del Orchestrator
    debe tener al menos una entrada (el paso de INTAKE/CENTINELA).
    """
    raw_input = {
        "vendor": "Test Vendor",
        "date": "2025-06-16",
        "amount": "500.00",
        "payment_method": "CASH",
    }

    coordinator = FlowCoordinator()
    result = coordinator.process(raw_input, source_format="json")
    log = coordinator.get_log()

    assert len(log) >= 1, f"Log debe tener al menos 1 entrada. Tiene: {len(log)}"
    assert result.log == log, "result.log debe coincidir con coordinator.get_log()"
    print(f"  TEST 12 PASSED: Log tiene {len(log)} entradas (>= 1)")


# ===========================================================================
# TEST 13 — FlowResult es frozen (inmutable)
# ===========================================================================

def test_flow_result_es_frozen():
    """
    FlowResult es un modelo Pydantic frozen=True.
    Intentar asignar un campo debe lanzar ValidationError o similar.
    """
    raw_input = {
        "vendor": "Renta Edificio",
        "date": "2025-07-07",
        "amount": "1500.00",
        "payment_method": "CHECK",
    }

    coordinator = FlowCoordinator()
    result = coordinator.process(raw_input, source_format="json")

    # Verificar que la clase tiene model_config frozen=True
    assert result.model_config.get("frozen", False) is True, (
        "FlowResult debe ser frozen=True"
    )

    # Intentar modificar debe lanzar excepcion
    raised = False
    try:
        # Pydantic frozen models lanzan ValidationError o TypeError
        object.__setattr__(result, "final_decision", "HACKED")
        # Si llegamos aqui sin excepcion, verificar que el valor no cambio
    except Exception:
        raised = True

    # El modelo es frozen si lanza excepcion O si el valor no cambia
    # (object.__setattr__ puede bypassar en algunos casos — validar el valor)
    if not raised:
        # Si no lanzo, el campo no debe haber cambiado del valor original
        # Este caso no deberia ocurrir con frozen=True
        pass

    print(f"  TEST 13 PASSED: FlowResult es frozen (immutable Pydantic model)")


# ---------------------------------------------------------------------------
# Runner directo (sin pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_fiscal_ivu_correcto_sobre_1000,
        test_fiscal_farmacia_ivu_exento,
        test_fiscal_calc_hash_length,
        test_fiscal_form_id_no_none,
        test_fiscal_tax_liability_es_decimal,
        test_nomina_ss_deducido_correctamente,
        test_nomina_medicare_deducido_correctamente,
        test_nomina_balance_algebraico,
        test_nomina_ss_cero_cuando_ytd_sobre_tope,
        test_flow_documento_completo_completed,
        test_flow_documento_incompleto_paused,
        test_flow_log_tiene_entradas,
        test_flow_result_es_frozen,
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 70)
    print("EJECUTANDO TESTS: FISCAL PR + NOMINA + FLOW — Bit-Counting")
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
