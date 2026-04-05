# =============================================================================
# agents/tests_clasificador_auditor.py
# Tests para CLASIFICADOR y AUDITOR del sistema Bit-Counting.
#
# Todos los tests deben pasar.
# Ejecutar: python -m pytest agents/tests_clasificador_auditor.py -v
#           o directamente: python agents/tests_clasificador_auditor.py
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agents.clasificador import ClasificadorAgent, get_chart_of_accounts, get_account
from agents.auditor import AuditorAgent
from agents.messages import (
    ClassifierOutput,
    EntryType,
    IntakeOutput,
    SourceFormat,
    AuditorVerification,
)
from agents.exceptions import AgentScopeError


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def make_intake(
    vendor: str | None = "Test Vendor",
    amount: str = "1000.00",
    tax_amount: str | None = None,
    date: str | None = "2025-06-15",
    payment_method: str | None = "CHECK",
    confidence: str = "0.90",
) -> IntakeOutput:
    """Factory para IntakeOutput de prueba."""
    return IntakeOutput(
        message_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        source_agent="INTAKE",
        target_agent="CENTINELA",
        vendor=vendor,
        date=date,
        amount=Decimal(amount),
        tax_amount=Decimal(tax_amount) if tax_amount else None,
        payment_method=payment_method,
        confidence=Decimal(confidence),
        source_format=SourceFormat.JSON,
    )


clasificador = ClasificadorAgent()
auditor = AuditorAgent()


# ===========================================================================
# TEST 1 — CLASIFICADOR asigna debito y credito correctos para gasto de renta
# ===========================================================================

def test_clasificador_renta():
    """
    Gasto de renta de oficina:
    - DEBITO:  5200 (Renta)
    - CREDITO: 1000 (Efectivo) porque payment_method=CHECK
    """
    intake = make_intake(vendor="Renta Oficina Centro", amount="2000.00", payment_method="CHECK")
    debit, credit = clasificador.classify(intake)

    assert debit.entry_type == EntryType.DEBIT,  "El primer asiento debe ser DEBITO"
    assert credit.entry_type == EntryType.CREDIT, "El segundo asiento debe ser CREDITO"
    assert debit.account_code == "5200", f"Renta debe ir a 5200, got: {debit.account_code}"
    # CHECK → efectivo (1000)
    assert credit.account_code == "1000", f"Pago con cheque debe creditar 1000, got: {credit.account_code}"
    assert debit.amount == credit.amount, "Partida doble debe estar balanceada"
    assert debit.amount == Decimal("2000.00")
    print("  TEST 1 PASSED: CLASIFICADOR asigna debito 5200 y credito 1000 para renta")


# ===========================================================================
# TEST 2 — CLASIFICADOR asigna COGS para compra de inventario
# ===========================================================================

def test_clasificador_inventario():
    """
    Compra de inventario:
    - DEBITO:  1200 (Inventario)
    - CREDITO: 2000 (Cuentas por Pagar) cuando payment_method=CREDIT_CARD
    """
    intake = make_intake(
        vendor="Compra Inventario Distribuidora PR",
        amount="5000.00",
        payment_method="CREDIT_CARD",
    )
    debit, credit = clasificador.classify(intake)

    assert debit.account_code == "1200", f"Inventario debe ir a 1200, got: {debit.account_code}"
    assert credit.account_code == "2000", f"Credito tarjeta debe ir a 2000, got: {credit.account_code}"
    assert debit.entry_type == EntryType.DEBIT
    assert credit.entry_type == EntryType.CREDIT
    assert debit.amount == credit.amount == Decimal("5000.00")
    print("  TEST 2 PASSED: CLASIFICADOR asigna 1200 (Inventario) para compra de mercancia")


# ===========================================================================
# TEST 3 — CLASIFICADOR asigna IVU por pagar correctamente
# ===========================================================================

def test_clasificador_ivu_por_pagar():
    """
    IVU cobrado a clientes → IVU por Pagar (2100).
    El asiento de partida doble reduce ingresos de ventas (4000) en el credito.
    """
    intake = make_intake(
        vendor="IVU cobrado clientes ventas",
        amount="1150.00",
        tax_amount="115.00",
        payment_method="CASH",
    )
    debit, credit = clasificador.classify(intake)

    # The IVU collected rule produces debit=4000, credit=2100
    assert credit.account_code == "2100", (
        f"IVU por pagar debe ir a credito 2100, got: {credit.account_code}"
    )
    assert debit.amount == credit.amount, "Partida doble debe balancear"
    print("  TEST 3 PASSED: CLASIFICADOR asigna IVU por Pagar (2100) correctamente")


# ===========================================================================
# TEST 4 — Double-entry balance: debit.amount == credit.amount siempre
# ===========================================================================

def test_double_entry_balance():
    """
    Varios tipos de transacciones — la partida doble siempre balancea.
    """
    test_cases = [
        ("Renta Oficina", "1500.00", "CHECK"),
        ("Nomina ADP", "8000.00", "ACH"),
        ("Seguro Triple-S", "450.00", "CHECK"),
        ("Google Ads publicidad", "200.00", "CREDIT_CARD"),
        ("Honorarios CPA Contador", "750.00", "CHECK"),
    ]
    for vendor, amount_str, pm in test_cases:
        intake = make_intake(vendor=vendor, amount=amount_str, payment_method=pm)
        debit, credit = clasificador.classify(intake)
        assert debit.amount == credit.amount, (
            f"Balance roto para '{vendor}': debit={debit.amount}, credit={credit.amount}"
        )
        assert debit.entry_type == EntryType.DEBIT
        assert credit.entry_type == EntryType.CREDIT
    print("  TEST 4 PASSED: Double-entry siempre balanceado (debit.amount == credit.amount)")


# ===========================================================================
# TEST 5 — CLASIFICADOR no puede crear cuenta nueva (AgentScopeError)
# ===========================================================================

def test_clasificador_no_crea_cuenta_nueva():
    """
    Si el vendor esta completamente vacio (None) y no hay ningun keyword,
    CLASIFICADOR debe lanzar AgentScopeError — nunca crea cuentas nuevas.

    Nota: Con vendor=None y texto vacio, _match_rule devuelve None (no hay
    keywords que coincidan y no hay vendor_lower text), lo que dispara el error.
    """
    intake = IntakeOutput(
        message_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        source_agent="INTAKE",
        target_agent="CENTINELA",
        vendor=None,        # sin vendor
        date="2025-06-15",
        amount=Decimal("100.00"),
        confidence=Decimal("0.50"),
        source_format=SourceFormat.UNKNOWN,
    )

    # With vendor=None, vendor_lower="", _match_rule fallback requires vendor_lower non-empty
    # Patch: we call the internal _match_rule directly to force None return
    result = clasificador._match_rule("", Decimal("100.00"), None)
    # With empty string, no keywords match AND vendor_lower is falsy → returns None
    assert result is None, (
        "Con vendor vacio, _match_rule debe retornar None (no puede clasificar)"
    )

    # Verify AgentScopeError is raised during classify()
    with pytest.raises(AgentScopeError):
        clasificador.classify(intake)
    print("  TEST 5 PASSED: CLASIFICADOR lanza AgentScopeError sin poder clasificar")


# ===========================================================================
# TEST 6 — AUDITOR verifica balance algebraico exacto
# ===========================================================================

def test_auditor_balance_algebraico_ok():
    """
    AUDITOR confirma algebraic_balance_ok=True cuando debit.amount == credit.amount.
    """
    intake = make_intake(vendor="Renta Oficina Centro", amount="1200.00", payment_method="CHECK")
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert verification.algebraic_balance_ok is True, (
        "AUDITOR debe confirmar balance algebraico OK"
    )
    assert "algebraic_balance" in verification.checks_passed
    assert verification.verified is True, f"Discrepancias: {verification.discrepancies}"
    print("  TEST 6 PASSED: AUDITOR verifica balance algebraico exacto correctamente")


# ===========================================================================
# TEST 7 — AUDITOR detecta balance roto (debit != credit)
# ===========================================================================

def test_auditor_detecta_balance_roto():
    """
    Si debit.amount != credit.amount, AUDITOR debe detectarlo y marcar
    algebraic_balance_ok=False y verified=False.
    """
    intake = make_intake(vendor="Renta Oficina", amount="1000.00")
    debit, credit = clasificador.classify(intake)

    # Tamper: create a credit entry with a different amount
    tampered_credit = ClassifierOutput(
        message_id=credit.message_id,
        timestamp=credit.timestamp,
        source_agent=credit.source_agent,
        target_agent=credit.target_agent,
        account_code=credit.account_code,
        account_name=credit.account_name,
        entry_type=EntryType.CREDIT,
        amount=Decimal("999.00"),   # <-- different amount!
        rule_ref=credit.rule_ref,
        confidence=credit.confidence,
        reasoning=credit.reasoning,
        classified_intake_id=credit.classified_intake_id,
    )

    verification = auditor.verify(debit, tampered_credit, intake)

    assert verification.algebraic_balance_ok is False, (
        "AUDITOR debe detectar que el balance esta roto"
    )
    assert verification.verified is False
    assert any("BALANCE ROTO" in d for d in verification.discrepancies), (
        f"Debe haber discrepancia de balance. Got: {verification.discrepancies}"
    )
    print("  TEST 7 PASSED: AUDITOR detecta balance roto (debit != credit)")


# ===========================================================================
# TEST 8 — AUDITOR detecta IVU incorrecto (tolerancia ±$0.01)
# ===========================================================================

def test_auditor_detecta_ivu_incorrecto():
    """
    Si tax_amount en intake no coincide con 11.5% del monto base (±$0.01),
    AUDITOR debe reportar discrepancia.
    """
    # Base amount = $1000, IVU correcto = $115.00
    # Reportamos $120.00 (incorrecto)
    intake = make_intake(
        vendor="Suplidores PR",
        amount="1000.00",
        tax_amount="120.00",   # incorrecto: deberia ser 115.00
        payment_method="CHECK",
    )
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert any("IVU INCORRECTO" in d for d in verification.discrepancies), (
        f"AUDITOR debe detectar IVU incorrecto. Discrepancias: {verification.discrepancies}"
    )
    print("  TEST 8 PASSED: AUDITOR detecta IVU incorrecto (tolerancia ±$0.01)")


def test_auditor_acepta_ivu_correcto():
    """
    IVU correcto (exactamente 11.5% de base) debe pasar sin discrepancia de IVU.
    """
    # Base = $1000.00, IVU = $115.00 exacto
    intake = make_intake(
        vendor="Suplidores PR",
        amount="1000.00",
        tax_amount="115.00",
        payment_method="CHECK",
    )
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert not any("IVU INCORRECTO" in d for d in verification.discrepancies), (
        f"IVU correcto no debe producir discrepancia. Got: {verification.discrepancies}"
    )
    assert "ivu_calculation_correct" in verification.checks_passed
    print("  TEST 8b PASSED: AUDITOR acepta IVU correcto ($115.00 = 11.5% de $1000.00)")


# ===========================================================================
# TEST 9 — AUDITOR flags transaccion en dia no laborable PR
# ===========================================================================

def test_auditor_flag_dia_no_laborable():
    """
    Transaccion con fecha de dia feriado PR debe producir discrepancia.
    Usamos Navidad 2025: 2025-12-25.
    """
    intake = make_intake(
        vendor="Renta Oficina",
        amount="1200.00",
        date="2025-12-25",   # Christmas Day — dia no laborable PR
        payment_method="CHECK",
    )
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert any("DIA NO LABORABLE" in d for d in verification.discrepancies), (
        f"AUDITOR debe flagear dia no laborable PR. Discrepancias: {verification.discrepancies}"
    )
    print("  TEST 9 PASSED: AUDITOR flags transaccion en dia no laborable PR (Navidad)")


# ===========================================================================
# TEST 10 — AUDITOR fraud flag: vendor nuevo + monto inusual
# ===========================================================================

def test_auditor_fraud_flag_vendor_nuevo_monto_inusual():
    """
    Vendedor completamente desconocido (no coincide con ningun keyword) +
    monto > $5,000 debe disparar fraud_flag.
    """
    intake = make_intake(
        vendor="XYZ Corp Desconocida LLC",    # no matches any known keyword
        amount="10000.00",                     # > $5,000 threshold
        payment_method="WIRE",
        date="2025-06-15",
    )
    # CLASIFICADOR clasificara como Gastos Varios (5900) — fallback
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert len(verification.fraud_flags) > 0, (
        "AUDITOR debe emitir fraud_flag para vendedor nuevo + monto inusual"
    )
    assert any("FRAUDE POTENCIAL" in f for f in verification.fraud_flags), (
        f"Fraud flag esperado. Got: {verification.fraud_flags}"
    )
    print("  TEST 10 PASSED: AUDITOR fraud flag para vendedor nuevo + monto inusual")


# ===========================================================================
# TEST 11 — AUDITOR pasa verificacion para transaccion limpia
# ===========================================================================

def test_auditor_transaccion_limpia():
    """
    Transaccion limpia:
    - Vendedor conocido
    - Monto razonable
    - Fecha dia laborable
    - IVU correcto (o sin IVU)
    - Balance algebraico OK
    Debe producir verified=True, sin discrepancias ni fraud_flags.
    """
    intake = make_intake(
        vendor="Renta Oficina San Juan",
        amount="1500.00",
        tax_amount=None,         # sin IVU para simplificar
        date="2025-06-16",       # lunes — dia laborable
        payment_method="CHECK",
        confidence="0.92",
    )
    debit, credit = clasificador.classify(intake)
    verification = auditor.verify(debit, credit, intake)

    assert verification.verified is True, (
        f"Transaccion limpia debe pasar verificacion. "
        f"Discrepancias: {verification.discrepancies}, Fraud: {verification.fraud_flags}"
    )
    assert verification.algebraic_balance_ok is True
    assert len(verification.discrepancies) == 0
    assert len(verification.fraud_flags) == 0
    print("  TEST 11 PASSED: AUDITOR pasa verificacion para transaccion limpia")


# ===========================================================================
# TEST 12 — Flujo completo: INTAKE data → CLASIFICADOR → AUDITOR → todo OK
# ===========================================================================

def test_flujo_completo_intake_clasificador_auditor():
    """
    Simula el flujo completo del sistema:
    1. Datos de INTAKE (simulados) → IntakeOutput
    2. IntakeOutput → CLASIFICADOR → (debit, credit)
    3. (debit, credit, intake) → AUDITOR → AuditorVerification
    4. Verificar que todo esta OK
    """
    # Paso 1: IntakeOutput (como si viniera del agente INTAKE real)
    intake = IntakeOutput(
        message_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        source_agent="INTAKE",
        target_agent="CENTINELA",
        vendor="Renta Oficina Hato Rey",
        date="2025-07-10",          # dia laborable (jueves)
        amount=Decimal("2200.00"),
        tax_amount=None,             # sin IVU en renta
        payment_method="CHECK",
        confidence=Decimal("0.90"),
        missing_fields=(),
        critical_fields_missing=False,
        source_format=SourceFormat.PDF,
    )

    # Paso 2: CLASIFICADOR
    debit, credit = clasificador.classify(intake)

    assert debit.entry_type == EntryType.DEBIT
    assert credit.entry_type == EntryType.CREDIT
    assert debit.amount == credit.amount == Decimal("2200.00")
    assert debit.account_code == "5200", f"Renta→5200, got: {debit.account_code}"
    assert credit.account_code == "1000", f"CHECK→1000, got: {credit.account_code}"
    assert debit.classified_intake_id == intake.message_id
    assert credit.classified_intake_id == intake.message_id

    # Paso 3: AUDITOR
    verification = auditor.verify(debit, credit, intake)

    assert isinstance(verification, AuditorVerification)
    assert verification.verified is True, (
        f"Flujo completo debe verificar OK. "
        f"Discrepancias: {verification.discrepancies}, Fraud: {verification.fraud_flags}"
    )
    assert verification.algebraic_balance_ok is True
    assert len(verification.discrepancies) == 0
    assert len(verification.fraud_flags) == 0
    assert verification.verified_classifier_id == debit.message_id

    # Paso 4: Verificar que la trazabilidad es completa
    assert verification.source_agent == "AUDITOR"
    assert verification.target_agent == "ORQUESTADOR"
    assert debit.source_agent == "CLASIFICADOR"
    assert debit.target_agent == "AUDITOR"

    print("  TEST 12 PASSED: Flujo completo INTAKE→CLASIFICADOR→AUDITOR todo OK")


# ---------------------------------------------------------------------------
# BONUS: Utility tests
# ---------------------------------------------------------------------------

def test_get_chart_of_accounts():
    """Chart of accounts tiene todas las cuentas esperadas."""
    chart = get_chart_of_accounts()
    required_codes = [
        "1000", "1100", "1200", "1300", "1500", "1600",
        "2000", "2100", "2200", "2300", "2400",
        "3000", "3100", "3900",
        "4000", "4100", "4900",
        "5000", "5100", "5200", "5300", "5400", "5500",
        "5600", "5700", "5800", "5900", "6000",
    ]
    for code in required_codes:
        assert code in chart, f"Codigo {code} faltante en el Plan de Cuentas"
    print("  BONUS TEST: Plan de Cuentas tiene todos los codigos requeridos")


def test_get_account_valid():
    """get_account() retorna detalles correctos."""
    acct = get_account("5200")
    assert acct["name"] == "Renta"
    assert acct["category"] == "EXPENSE"
    print("  BONUS TEST: get_account('5200') retorna detalles correctos")


def test_get_account_invalid():
    """get_account() lanza KeyError para codigo que no existe."""
    with pytest.raises(KeyError):
        get_account("9999")
    print("  BONUS TEST: get_account('9999') lanza KeyError correctamente")


def test_auditor_detect_anomalies():
    """detect_anomalies() flags monto > 3x el promedio historico."""
    intake = make_intake(vendor="Renta Oficina", amount="9000.00")
    history = [
        {"amount": Decimal("1000.00"), "type": "renta"},
        {"amount": Decimal("1100.00"), "type": "renta"},
        {"amount": Decimal("900.00"),  "type": "renta"},
    ]
    anomalies = auditor.detect_anomalies(intake, history)
    assert len(anomalies) > 0, "Monto 9x el promedio debe ser detectado como anomalia"
    assert any("ANOMALIA" in a for a in anomalies)
    print("  BONUS TEST: detect_anomalies() detecta monto > 3x promedio historico")


# ---------------------------------------------------------------------------
# Runner directo (sin pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_clasificador_renta,
        test_clasificador_inventario,
        test_clasificador_ivu_por_pagar,
        test_double_entry_balance,
        test_clasificador_no_crea_cuenta_nueva,
        test_auditor_balance_algebraico_ok,
        test_auditor_detecta_balance_roto,
        test_auditor_detecta_ivu_incorrecto,
        test_auditor_acepta_ivu_correcto,
        test_auditor_flag_dia_no_laborable,
        test_auditor_fraud_flag_vendor_nuevo_monto_inusual,
        test_auditor_transaccion_limpia,
        test_flujo_completo_intake_clasificador_auditor,
        test_get_chart_of_accounts,
        test_get_account_valid,
        test_get_account_invalid,
        test_auditor_detect_anomalies,
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 70)
    print("EJECUTANDO TESTS: CLASIFICADOR + AUDITOR — Bit-Counting")
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
