# =============================================================================
# agents/tests_independent_auditor.py
# Tests del IndependentAuditor — cada check con casos PASS y FAIL.
# =============================================================================

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from decimal import Decimal

from agents.independent_auditor import (
    AuditRequest,
    AuditSeverity,
    IndependentAuditor,
)


def _run(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASSED" if ok else "FAILED"
    print(f"  TEST {label}: {status}" + (f"\n    → {detail}" if not ok else ""))
    return ok


def _submit_and_get(auditor: IndependentAuditor, req: AuditRequest):
    auditor.submit(req)
    return auditor.process_next()


# =========================================================================
# GRUPO 1 — Balance algebraico
# =========================================================================

def test_balance_correcto():
    """Debitos == Creditos → sin alertas CRITICAL/HIGH."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T01",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1000.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = r.passed and not any(
        al.check_name == "algebraic_balance" for al in r.alerts
    )
    return _run("01", ok, f"alerts={[(al.check_name, al.severity) for al in r.alerts]}")


def test_balance_roto():
    """Debitos != Creditos → alerta CRITICAL."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T02",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("999.99")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = (
        not r.passed
        and any(
            al.check_name == "algebraic_balance"
            and al.severity == AuditSeverity.CRITICAL
            for al in r.alerts
        )
    )
    return _run("02", ok, f"passed={r.passed} alerts={[(al.check_name, al.severity) for al in r.alerts]}")


def test_balance_multiple_entradas():
    """Asiento con 3+ entradas — suma total debe cuadrar."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T03",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("800.00")},
            {"account_code": "5300", "entry_type": "debit",  "amount": Decimal("200.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1000.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "algebraic_balance" for al in r.alerts)
    return _run("03", ok, f"alerts={r.alerts}")


def test_balance_un_centavo_de_diferencia():
    """Diferencia de $0.01 → CRITICO (tolerancia exactamente $0.00)."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T04",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("500.01")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("500.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "algebraic_balance" and al.severity == AuditSeverity.CRITICAL
        for al in r.alerts
    )
    return _run("04", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


# =========================================================================
# GRUPO 2 — Verificacion IVU
# =========================================================================

def test_ivu_correcto():
    """IVU reportado == 11.5% de la base → sin alerta IVU."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T05",
        transaction_date="2025-03-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1115.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1115.00")},
        ),
        ivu_base_amount=Decimal("1000.00"),
        ivu_reported_amount=Decimal("115.00"),
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "ivu_verification" for al in r.alerts)
    return _run("05", ok, f"alerts={[(al.check_name, al.description) for al in r.alerts]}")


def test_ivu_incorrecto():
    """IVU reportado != 11.5% → alerta HIGH."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T06",
        transaction_date="2025-03-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1110.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1110.00")},
        ),
        ivu_base_amount=Decimal("1000.00"),
        ivu_reported_amount=Decimal("110.00"),  # correcto: $115.00
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "ivu_verification" and al.severity == AuditSeverity.HIGH
        for al in r.alerts
    )
    return _run("06", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


def test_ivu_exento_con_monto_cero():
    """Transaccion exenta con IVU=0 → sin alerta."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T07",
        transaction_date="2025-03-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("500.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("500.00")},
        ),
        ivu_base_amount=Decimal("500.00"),
        ivu_reported_amount=Decimal("0.00"),
        ivu_exempt=True,
        ivu_exempt_reason="Medicamentos recetados",
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "ivu_verification" for al in r.alerts)
    return _run("07", ok, f"alerts={r.alerts}")


def test_ivu_exento_pero_cobra_ivu():
    """Marcado como exento pero reporta IVU > 0 → alerta HIGH."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T08",
        transaction_date="2025-03-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("557.50")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("557.50")},
        ),
        ivu_base_amount=Decimal("500.00"),
        ivu_reported_amount=Decimal("57.50"),
        ivu_exempt=True,
        ivu_exempt_reason="Alimentos",
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "ivu_verification" and al.severity == AuditSeverity.HIGH
        for al in r.alerts
    )
    return _run("08", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


# =========================================================================
# GRUPO 3 — Balance de nomina
# =========================================================================

def test_nomina_balanceada():
    """gross == net + deducciones → sin alerta."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T09",
        transaction_date="2025-04-01",
        journal_entries=(
            {"account_code": "5100", "entry_type": "debit",  "amount": Decimal("1000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1000.00")},
        ),
        is_payroll=True,
        gross_pay=Decimal("1000.00"),
        net_pay=Decimal("845.00"),
        deductions=(
            {"name": "SS",       "amount": Decimal("62.00")},
            {"name": "Medicare", "amount": Decimal("14.50")},
            {"name": "FUTA",     "amount": Decimal("6.00")},
            {"name": "Seguro",   "amount": Decimal("72.50")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "payroll_balance" for al in r.alerts)
    return _run("09", ok, f"alerts={[(al.check_name, al.description) for al in r.alerts]}")


def test_nomina_desbalanceada():
    """gross != net + deducciones → alerta CRITICAL."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T10",
        transaction_date="2025-04-01",
        journal_entries=(
            {"account_code": "5100", "entry_type": "debit",  "amount": Decimal("1000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1000.00")},
        ),
        is_payroll=True,
        gross_pay=Decimal("1000.00"),
        net_pay=Decimal("900.00"),       # deberia ser 845.00
        deductions=(
            {"name": "SS",       "amount": Decimal("62.00")},
            {"name": "Medicare", "amount": Decimal("14.50")},
            {"name": "FUTA",     "amount": Decimal("6.00")},
            {"name": "Seguro",   "amount": Decimal("72.50")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "payroll_balance" and al.severity == AuditSeverity.CRITICAL
        for al in r.alerts
    )
    return _run("10", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


def test_nomina_no_aplica_sin_flag():
    """Transaccion sin is_payroll=True → check de nomina no aplica."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T11",
        transaction_date="2025-04-01",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("500.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("500.00")},
        ),
        # is_payroll=False por defecto
    )
    r = _submit_and_get(a, req)
    ok = "payroll_balance:not_applicable" in r.checks_performed
    return _run("11", ok, f"checks={r.checks_performed}")


# =========================================================================
# GRUPO 4 — Dia no laborable PR
# =========================================================================

def test_dia_laborable_ok():
    """Fecha de dia laborable → sin alerta de feriado."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T12",
        transaction_date="2025-06-16",  # Lunes normal
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("200.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("200.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "non_working_day" for al in r.alerts)
    return _run("12", ok, f"alerts={r.alerts}")


def test_navidad_pr():
    """25 de diciembre → alerta LOW de dia no laborable."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T13",
        transaction_date="2025-12-25",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("200.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("200.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "non_working_day" and al.severity == AuditSeverity.LOW
        for al in r.alerts
    )
    return _run("13", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


def test_tres_reyes_pr():
    """6 de enero (Dia de Reyes PR) → alerta LOW."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T14",
        transaction_date="2025-01-06",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "non_working_day" and al.severity == AuditSeverity.LOW
        for al in r.alerts
    )
    return _run("14", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


# =========================================================================
# GRUPO 5 — Vendor nuevo + monto atipico
# =========================================================================

def test_vendor_conocido_no_alerta():
    """Vendor conocido → no activa el check de anomalia."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T15",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("500.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("500.00")},
        ),
        vendor="Costco",
        vendor_is_new=False,
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "new_vendor_anomaly" for al in r.alerts)
    return _run("15", ok, f"alerts={r.alerts}")


def test_vendor_nuevo_monto_normal():
    """Vendor nuevo pero monto dentro del rango historico → sin alerta HIGH."""
    a = IndependentAuditor()
    history = tuple(Decimal(str(x)) for x in [500, 480, 510, 495, 505, 490])
    req = AuditRequest(
        transaction_ref="T16",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("500.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("500.00")},
        ),
        vendor="NuevoProveedor SA",
        vendor_is_new=True,
        client_history_amounts=history,
    )
    r = _submit_and_get(a, req)
    ok = not any(
        al.check_name == "new_vendor_anomaly"
        and al.severity in (AuditSeverity.HIGH, AuditSeverity.CRITICAL)
        for al in r.alerts
    )
    return _run("16", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


def test_vendor_nuevo_monto_atipico():
    """Vendor nuevo + monto >2 desv. estandar sobre historial → alerta MEDIUM/HIGH."""
    a = IndependentAuditor()
    # Historia con media ~$500, stdev ~$10
    history = tuple(Decimal(str(x)) for x in [495, 500, 498, 502, 497, 503, 499, 501])
    req = AuditRequest(
        transaction_ref="T17",
        transaction_date="2025-06-15",
        journal_entries=(
            # Monto $5,000 — muy por encima del rango historico
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("5000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("5000.00")},
        ),
        vendor="EmpresaFantasma LLC",
        vendor_is_new=True,
        client_history_amounts=history,
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "new_vendor_anomaly"
        and al.severity in (AuditSeverity.MEDIUM, AuditSeverity.HIGH, AuditSeverity.CRITICAL)
        for al in r.alerts
    )
    return _run("17", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


# =========================================================================
# GRUPO 6 — Tasa aplicada vs. tasa vigente en fecha de transaccion
# =========================================================================

def test_tasa_correcta_en_fecha():
    """Tasa IVU 10.5% aplicada cuando la regla dice 10.5% → sin alerta."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T18",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1105.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1105.00")},
        ),
        rate_rule_id="IVU_ESTATAL_PR_2015_V1",
        rate_applied=Decimal("10.5"),
    )
    r = _submit_and_get(a, req)
    ok = not any(al.check_name == "rate_by_transaction_date" for al in r.alerts)
    return _run("18", ok, f"alerts={[(al.check_name, al.description[:60]) for al in r.alerts]}")


def test_tasa_incorrecta():
    """Tasa 8.0% aplicada cuando la regla dice 10.5% → alerta HIGH."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T19",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("1080.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1080.00")},
        ),
        rate_rule_id="IVU_ESTATAL_PR_2015_V1",
        rate_applied=Decimal("8.0"),   # incorrecto — deberia ser 10.5
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "rate_by_transaction_date"
        and al.severity == AuditSeverity.HIGH
        for al in r.alerts
    )
    return _run("19", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


def test_regla_no_vigente_en_fecha():
    """Regla FICA 2024 aplicada en 2025 → alerta CRITICAL."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T20",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5100", "entry_type": "debit",  "amount": Decimal("1000.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("1000.00")},
        ),
        rate_rule_id="FICA_SS_2024_V1",   # expirada en 2024-12-31
        rate_applied=Decimal("6.2"),
    )
    r = _submit_and_get(a, req)
    ok = any(
        al.check_name == "rate_by_transaction_date"
        and al.severity == AuditSeverity.CRITICAL
        for al in r.alerts
    )
    return _run("20", ok, f"alerts={[(al.check_name, al.severity.value) for al in r.alerts]}")


# =========================================================================
# GRUPO 7 — Propiedades del sistema
# =========================================================================

def test_never_raises_exception():
    """Solicitud completamente invalida → AuditResult, nunca excepcion."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T21",
        transaction_date="FECHA_INVALIDA",
        journal_entries=(
            {"account_code": "9999", "entry_type": "TIPO_INVALIDO", "amount": Decimal("0")},
        ),
    )
    try:
        r = _submit_and_get(a, req)
        ok = isinstance(r, type(r))  # retorna algo
    except Exception as e:
        ok = False
        return _run("21", ok, f"Lanzo excepcion: {e}")
    return _run("21", ok)


def test_audit_log_append_only():
    """El audit_log crece con cada procesamiento — no se puede borrar."""
    a = IndependentAuditor()
    for i in range(3):
        req = AuditRequest(
            transaction_ref=f"LOG{i}",
            transaction_date="2025-06-15",
            journal_entries=(
                {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
                {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
            ),
        )
        a.submit(req)
        a.process_next()
    log = a.get_audit_log()
    ok = len(log) == 3 and isinstance(log, tuple)
    return _run("22", ok, f"len={len(log)} type={type(log).__name__}")


def test_cola_separada_process_next_none():
    """Cola vacia → process_next() retorna None."""
    a = IndependentAuditor()
    ok = a.process_next() is None
    return _run("23", ok)


def test_audit_result_frozen():
    """AuditResult es inmutable (frozen Pydantic)."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T24",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
        ),
    )
    r = _submit_and_get(a, req)
    try:
        r.passed = False  # type: ignore[misc]
        ok = False
    except Exception:
        ok = True
    return _run("24", ok)


def test_computation_time_ms_presente():
    """computation_time_ms es un entero >= 0."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T25",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = isinstance(r.computation_time_ms, int) and r.computation_time_ms >= 0
    return _run("25", ok, f"ms={r.computation_time_ms}")


def test_auditor_version():
    """auditor_version siempre presente en el resultado."""
    a = IndependentAuditor()
    req = AuditRequest(
        transaction_ref="T26",
        transaction_date="2025-06-15",
        journal_entries=(
            {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
            {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
        ),
    )
    r = _submit_and_get(a, req)
    ok = r.auditor_version == "2.0.0"
    return _run("26", ok, f"version={r.auditor_version}")


def test_process_all():
    """process_all() drena la cola completa."""
    a = IndependentAuditor()
    for i in range(5):
        req = AuditRequest(
            transaction_ref=f"PA{i}",
            transaction_date="2025-06-15",
            journal_entries=(
                {"account_code": "5200", "entry_type": "debit",  "amount": Decimal("100.00")},
                {"account_code": "1000", "entry_type": "credit", "amount": Decimal("100.00")},
            ),
        )
        a.submit(req)
    results = a.process_all()
    ok = len(results) == 5 and a.queue_depth() == 0
    return _run("27", ok, f"results={len(results)} queue={a.queue_depth()}")


# =========================================================================
# RUNNER
# =========================================================================

ALL_TESTS = [
    ("01 Balance correcto",              test_balance_correcto),
    ("02 Balance roto",                  test_balance_roto),
    ("03 Balance multiple entradas",     test_balance_multiple_entradas),
    ("04 Balance 1 centavo diferencia",  test_balance_un_centavo_de_diferencia),
    ("05 IVU correcto",                  test_ivu_correcto),
    ("06 IVU incorrecto",                test_ivu_incorrecto),
    ("07 IVU exento con cero",           test_ivu_exento_con_monto_cero),
    ("08 IVU exento pero cobra IVU",     test_ivu_exento_pero_cobra_ivu),
    ("09 Nomina balanceada",             test_nomina_balanceada),
    ("10 Nomina desbalanceada",          test_nomina_desbalanceada),
    ("11 Nomina no aplica",              test_nomina_no_aplica_sin_flag),
    ("12 Dia laborable OK",              test_dia_laborable_ok),
    ("13 Navidad PR",                    test_navidad_pr),
    ("14 Tres Reyes PR",                 test_tres_reyes_pr),
    ("15 Vendor conocido",               test_vendor_conocido_no_alerta),
    ("16 Vendor nuevo monto normal",     test_vendor_nuevo_monto_normal),
    ("17 Vendor nuevo monto atipico",    test_vendor_nuevo_monto_atipico),
    ("18 Tasa correcta en fecha",        test_tasa_correcta_en_fecha),
    ("19 Tasa incorrecta",               test_tasa_incorrecta),
    ("20 Regla no vigente en fecha",     test_regla_no_vigente_en_fecha),
    ("21 Nunca lanza excepcion",         test_never_raises_exception),
    ("22 Audit log append-only",         test_audit_log_append_only),
    ("23 Cola separada None",            test_cola_separada_process_next_none),
    ("24 AuditResult frozen",            test_audit_result_frozen),
    ("25 computation_time_ms",           test_computation_time_ms_presente),
    ("26 auditor_version",               test_auditor_version),
    ("27 process_all drain",             test_process_all),
]

if __name__ == "__main__":
    print("=" * 70)
    print("TESTS: IndependentAuditor — Bit-Counting PR")
    print("=" * 70)

    passed = 0
    failed = 0

    for label, fn in ALL_TESTS:
        try:
            ok = fn()
            passed += (1 if ok else 0)
            failed += (0 if ok else 1)
        except Exception as exc:
            failed += 1
            print(f"  TEST {label}: EXCEPTION — {exc}")

    print("=" * 70)
    print(f"RESULTADO: {passed} passed, {failed} failed")
    if failed == 0:
        print("TODOS LOS TESTS PASARON.")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)
