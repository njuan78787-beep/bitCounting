# =============================================================================
# agents/tests_centinela_guardian.py
# Tests del CentinelaGuardian — cada trigger con escenarios reales de PR.
# =============================================================================

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.centinela_guardian import (
    CentinelaGuardian,
    ClientConfig,
    PauseTrigger,
    SurgicalPause,
    PauseRelease,
)


def _run(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASSED" if ok else "FAILED"
    msg = f"  TEST {label}: {status}"
    if not ok and detail:
        msg += f"\n    → {detail}"
    print(msg)
    return ok


def _base_evaluate(guardian, overrides=None):
    """Evaluacion base con parametros seguros que dan PROCEED."""
    kwargs = dict(
        transaction_id="TXN-001",
        client_id="CLI-PR-001",
        transaction_date="2025-06-15",
        amount=Decimal("500.00"),
        vendor="Costco PR",
        transaction_type="GASTO_OPERACIONAL",
        confidence=Decimal("0.92"),
        current_rule_id="IVU_ESTATAL_PR_2015_V1",
        history_90d=(),
        history_6m=(
            {"vendor": "Costco PR", "transaction_type": "GASTO_OPERACIONAL",
             "amount": Decimal("480.00"), "rule_applied": "IVU_ESTATAL_PR_2015_V1",
             "date": "2025-01-10"},
        ),
        cross_check_results=(),
        audit_result=None,
    )
    if overrides:
        kwargs.update(overrides)
    return guardian.evaluate(**kwargs)


# =========================================================================
# GRUPO 1 — Trigger 1: Contradiccion de regla en 90 dias
# =========================================================================

def test_trigger1_sin_contradiccion():
    """Misma regla aplicada en 90d → PROCEED."""
    g = CentinelaGuardian()
    history_90d = (
        {"vendor": "AT&T PR",  "transaction_type": "SERVICIO_TELECOM",
         "amount": Decimal("200.00"), "rule_applied": "IVU_ESTATAL_PR_2015_V1",
         "date": "2025-05-01"},
        {"vendor": "Claro PR", "transaction_type": "SERVICIO_TELECOM",
         "amount": Decimal("150.00"), "rule_applied": "IVU_ESTATAL_PR_2015_V1",
         "date": "2025-05-15"},
    )
    d = _base_evaluate(g, {
        "transaction_type": "SERVICIO_TELECOM",
        "current_rule_id":  "IVU_ESTATAL_PR_2015_V1",
        "history_90d":      history_90d,
    })
    ok = d.decision == "PROCEED"
    return _run("01", ok, f"decision={d.decision}")


def test_trigger1_contradiccion_activa():
    """Regla nueva diferente a la aplicada en 90d → PAUSE RULE_CONTRADICTION_90D."""
    g = CentinelaGuardian()
    # En los ultimos 90 dias usamos la regla SS_2024, ahora usamos SS_2025
    history_90d = (
        {"vendor": "Empleado Juan", "transaction_type": "NOMINA",
         "amount": Decimal("2000.00"), "rule_applied": "FICA_SS_2024_V1",
         "date": "2025-03-01"},
        {"vendor": "Empleado Maria", "transaction_type": "NOMINA",
         "amount": Decimal("1800.00"), "rule_applied": "FICA_SS_2024_V1",
         "date": "2025-04-01"},
    )
    d = _base_evaluate(g, {
        "transaction_id":   "NOMINA-Q1",
        "transaction_type": "NOMINA",
        "current_rule_id":  "FICA_SS_2025_V1",   # nueva regla
        "history_90d":      history_90d,
        "history_6m":       history_90d,
        "amount":           Decimal("2100.00"),
        "vendor":           "Empleado Carlos",
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.RULE_CONTRADICTION_90D
    return _run("02", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger1_pausa_quirurgica():
    """La pausa bloquea solo la transaccion afectada, no otras del cliente."""
    g = CentinelaGuardian()
    history_90d = (
        {"vendor": "Empleado X", "transaction_type": "NOMINA",
         "amount": Decimal("1000.00"), "rule_applied": "FICA_SS_2024_V1",
         "date": "2025-04-01"},
    )
    d = _base_evaluate(g, {
        "transaction_id":   "NOMINA-BLOQUEADA",
        "transaction_type": "NOMINA",
        "current_rule_id":  "FICA_SS_2025_V1",
        "history_90d":      history_90d,
        "amount":           Decimal("1500.00"),
    })
    # Esta transaccion esta bloqueada
    bloqueada = g.is_transaction_blocked("NOMINA-BLOQUEADA")
    # Otra transaccion del mismo cliente NO esta bloqueada
    no_bloqueada = g.is_transaction_blocked("OTRA-TXN-999")
    ok = bloqueada and not no_bloqueada
    return _run("03", ok, f"bloqueada={bloqueada} no_bloqueada={no_bloqueada}")


# =========================================================================
# GRUPO 2 — Trigger 2: Confidence < umbral del cliente
# =========================================================================

def test_trigger2_confidence_sobre_umbral():
    """Confidence 0.92 con umbral 0.75 → PROCEED."""
    g = CentinelaGuardian()
    cfg = ClientConfig(client_id="CLI-001", confidence_threshold=Decimal("0.75"))
    d = _base_evaluate(g, {
        "confidence":    Decimal("0.92"),
        "client_config": cfg,
    })
    ok = d.decision == "PROCEED"
    return _run("04", ok, f"decision={d.decision}")


def test_trigger2_confidence_bajo_umbral_default():
    """Confidence 0.60 con umbral default 0.75 → PAUSE LOW_CONFIDENCE."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-LOW-CONF",
        "confidence":     Decimal("0.60"),
        "vendor":         "Farmacia San Pablo",
        "amount":         Decimal("350.00"),
        "history_6m":     (),
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.LOW_CONFIDENCE
    return _run("05", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger2_umbral_personalizado_alto():
    """Cliente con umbral alto 0.95 — confidence 0.88 → PAUSE."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-STRICTO",
        confidence_threshold=Decimal("0.95"),
    )
    d = _base_evaluate(g, {
        "transaction_id": "TXN-STRICT",
        "confidence":     Decimal("0.88"),
        "client_config":  cfg,
        "history_6m":     (),
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.LOW_CONFIDENCE
    return _run("06", ok, f"decision={d.decision} confidence=0.88 umbral=0.95")


def test_trigger2_umbral_personalizado_bajo():
    """Cliente con umbral bajo 0.50 — confidence 0.60 → PROCEED."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-RELAX",
        confidence_threshold=Decimal("0.50"),
    )
    d = _base_evaluate(g, {
        "confidence":    Decimal("0.60"),
        "client_config": cfg,
    })
    ok = d.decision == "PROCEED"
    return _run("07", ok, f"decision={d.decision}")


# =========================================================================
# GRUPO 3 — Trigger 3: Sin precedente en 6 meses
# =========================================================================

def test_trigger3_con_precedente():
    """Vendor conocido en historial 6m → PROCEED aunque monto sea alto."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-002",
        no_precedent_threshold=Decimal("5000.00"),
    )
    history_6m = (
        {"vendor": "Lawyers PR LLC", "transaction_type": "HONORARIOS",
         "amount": Decimal("8000.00"), "rule_applied": "IVU_ESTATAL_PR_2015_V1",
         "date": "2025-02-01"},
    )
    d = _base_evaluate(g, {
        "transaction_id":   "TXN-HON",
        "vendor":           "Lawyers PR LLC",
        "transaction_type": "HONORARIOS",
        "amount":           Decimal("7500.00"),
        "history_6m":       history_6m,
        "client_config":    cfg,
    })
    ok = d.decision == "PROCEED"
    return _run("08", ok, f"decision={d.decision}")


def test_trigger3_sin_precedente_monto_alto():
    """Vendor nuevo, monto > umbral, sin precedente 6m → PAUSE NO_PRECEDENT_6M."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-003",
        no_precedent_threshold=Decimal("5000.00"),
    )
    d = _base_evaluate(g, {
        "transaction_id":   "TXN-NEW-VENDOR",
        "vendor":           "Consultores del Norte SA",
        "transaction_type": "CONSULTORIA",
        "amount":           Decimal("12000.00"),
        "history_6m":       (),
        "history_90d":      (),
        "client_config":    cfg,
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.NO_PRECEDENT_6M
    return _run("09", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger3_sin_precedente_monto_bajo():
    """Sin precedente pero monto < umbral → PROCEED (no pausar montos pequenos)."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-004",
        no_precedent_threshold=Decimal("5000.00"),
    )
    d = _base_evaluate(g, {
        "transaction_id":   "TXN-SMALL",
        "vendor":           "Quiosco Nuevo",
        "transaction_type": "GASTO_MENOR",
        "amount":           Decimal("45.00"),
        "history_6m":       (),
        "history_90d":      (),
        "client_config":    cfg,
    })
    ok = d.decision == "PROCEED"
    return _run("10", ok, f"decision={d.decision} amount=45 umbral=5000")


# =========================================================================
# GRUPO 4 — Trigger 4: Divergencia entre agentes
# =========================================================================

def test_trigger4_agentes_de_acuerdo():
    """Dos agentes con mismo resultado → PROCEED."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "cross_check_results": (
            {"agent": "CLASIFICADOR", "result": "5200"},
            {"agent": "AUDITOR",      "result": "5200"},
        ),
    })
    ok = d.decision == "PROCEED"
    return _run("11", ok, f"decision={d.decision}")


def test_trigger4_clasificador_vs_auditor():
    """CLASIFICADOR dice cuenta 5200, AUDITOR dice 5300 → PAUSE AGENT_DIVERGENCE."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-DIV",
        "cross_check_results": (
            {"agent": "CLASIFICADOR", "result": "5200"},
            {"agent": "AUDITOR",      "result": "5300"},
        ),
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.AGENT_DIVERGENCE
    return _run("12", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger4_divergencia_fiscal():
    """FISCAL calcula $115, NOMINA calcula $0 → PAUSE."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id":   "TXN-FISCAL-DIV",
        "transaction_type": "NOMINA_CON_IVU",
        "cross_check_results": (
            {"agent": "FISCAL_PR", "result": "115.00"},
            {"agent": "NOMINA",    "result": "0.00"},
        ),
        "amount": Decimal("1000.00"),
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.AGENT_DIVERGENCE
    return _run("13", ok, f"decision={d.decision} trigger={d.trigger}")


# =========================================================================
# GRUPO 5 — Trigger 5: AuditResult HIGH o CRITICAL
# =========================================================================

def test_trigger5_audit_pass():
    """AuditResult sin alertas HIGH/CRITICAL → PROCEED."""
    g = CentinelaGuardian()
    audit = {
        "passed": True,
        "alerts": [
            {"severity": "LOW", "check_name": "non_working_day",
             "description": "Navidad", "rule_ref": None}
        ],
    }
    d = _base_evaluate(g, {"audit_result": audit})
    ok = d.decision == "PROCEED"
    return _run("14", ok, f"decision={d.decision}")


def test_trigger5_audit_high():
    """AuditResult con alerta HIGH → PAUSE AUDIT_HIGH_CRITICAL."""
    g = CentinelaGuardian()
    audit = {
        "passed": False,
        "alerts": [
            {"severity": "HIGH", "check_name": "ivu_verification",
             "description": "IVU incorrecto", "expected": "115.00",
             "actual": "50.00", "rule_ref": "IVU_ESTATAL_PR_2015_V1"}
        ],
    }
    d = _base_evaluate(g, {
        "transaction_id": "TXN-IVU-ERROR",
        "audit_result": audit,
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.AUDIT_HIGH_CRITICAL
    return _run("15", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger5_audit_critical():
    """AuditResult con alerta CRITICAL (balance roto) → PAUSE."""
    g = CentinelaGuardian()
    audit = {
        "passed": False,
        "alerts": [
            {"severity": "CRITICAL", "check_name": "algebraic_balance",
             "description": "Debito $1000 != Credito $999.99",
             "expected": "1000.00", "actual": "999.99", "rule_ref": None}
        ],
    }
    d = _base_evaluate(g, {
        "transaction_id": "TXN-BALANCE-ROTO",
        "audit_result": audit,
    })
    ok = d.decision == "PAUSE" and d.trigger == PauseTrigger.AUDIT_HIGH_CRITICAL
    return _run("16", ok, f"decision={d.decision} trigger={d.trigger}")


def test_trigger5_precede_a_otros_triggers():
    """CRITICAL del AUDITOR tiene prioridad aunque haya confidence bajo."""
    g = CentinelaGuardian()
    audit = {
        "passed": False,
        "alerts": [{"severity": "CRITICAL", "check_name": "algebraic_balance",
                    "description": "Balance roto", "rule_ref": None}],
    }
    d = _base_evaluate(g, {
        "transaction_id": "TXN-DUAL-TRIGGER",
        "confidence":     Decimal("0.30"),   # tambien trigger 2
        "audit_result":   audit,
        "history_6m":     (),
    })
    # El AUDIT_HIGH_CRITICAL debe ser el trigger reportado (mayor prioridad)
    ok = d.trigger == PauseTrigger.AUDIT_HIGH_CRITICAL
    return _run("17", ok, f"trigger={d.trigger}")


# =========================================================================
# GRUPO 6 — Liberacion de pausa
# =========================================================================

def test_release_pause_correcta():
    """CPA libera pausa con credenciales validas → PauseRelease registrada."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-PARA-LIBERAR",
        "confidence":     Decimal("0.50"),
        "history_6m":     (),
    })
    assert d.decision == "PAUSE"

    release = g.release_pause(
        pause_id=d.pause_id,
        cpa_license="CPA-PR-12345",
        cpa_token="TOKEN-SEGURO-2025",
        interpretation_chosen="OPCION A",
        instruction="Documento verificado con el proveedor — es legitimo.",
        cpa_name="Juan Rodriguez CPA",
    )
    ok = (
        isinstance(release, PauseRelease)
        and release.pause_id == d.pause_id
        and release.released_by_license == "CPA-PR-12345"
        and not g.is_transaction_blocked("TXN-PARA-LIBERAR")
    )
    return _run("18", ok, f"released={release.released_by_license} blocked={g.is_transaction_blocked('TXN-PARA-LIBERAR')}")


def test_release_sin_licencia_falla():
    """release_pause sin cpa_license → ValueError."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {"transaction_id": "TXN-R2", "confidence": Decimal("0.40"), "history_6m": ()})
    try:
        g.release_pause(d.pause_id, "", "TOKEN12345", "A", "instruccion")
        ok = False
    except ValueError:
        ok = True
    return _run("19", ok)


def test_release_token_corto_falla():
    """release_pause con token < 8 chars → ValueError."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {"transaction_id": "TXN-R3", "confidence": Decimal("0.40"), "history_6m": ()})
    try:
        g.release_pause(d.pause_id, "CPA-001", "SHORT", "A", "instruccion")
        ok = False
    except ValueError:
        ok = True
    return _run("20", ok)


def test_release_sin_instruccion_falla():
    """release_pause sin instruction → ValueError."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {"transaction_id": "TXN-R4", "confidence": Decimal("0.40"), "history_6m": ()})
    try:
        g.release_pause(d.pause_id, "CPA-001", "TOKEN-LARGO", "A", "")
        ok = False
    except ValueError:
        ok = True
    return _run("21", ok)


def test_release_log_inmutable():
    """El release_log crece como append-only tuple."""
    g = CentinelaGuardian()
    for i in range(3):
        d = _base_evaluate(g, {
            "transaction_id": f"TXN-LOG{i}",
            "confidence": Decimal("0.40"),
            "history_6m": (),
        })
        g.release_pause(
            d.pause_id, f"CPA-{i:03d}", "TOKEN-PRUEBA",
            "OPCION A", "Verificado y aprobado."
        )
    log = g.get_release_log()
    ok = len(log) == 3 and isinstance(log, tuple)
    return _run("22", ok, f"len={len(log)} type={type(log).__name__}")


def test_release_ya_resuelta_falla():
    """Liberar una pausa ya liberada → KeyError."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {"transaction_id": "TXN-DOBLE", "confidence": Decimal("0.40"), "history_6m": ()})
    g.release_pause(d.pause_id, "CPA-001", "TOKEN-LARGO-OK", "A", "Primera vez.")
    try:
        g.release_pause(d.pause_id, "CPA-001", "TOKEN-LARGO-OK", "A", "Segunda vez.")
        ok = False
    except KeyError:
        ok = True
    return _run("23", ok)


# =========================================================================
# GRUPO 7 — Escalacion por SLA
# =========================================================================

def test_escalacion_por_sla():
    """Pausa con SLA vencido y cadena de escalacion → escalada al siguiente CPA."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-ESCALACION",
        sla_hours=2,
        cpa_primary="CPA-PRIMARIO",
        escalation_chain=("CPA-BACKUP", "CPA-DIRECTOR"),
    )
    d = _base_evaluate(g, {
        "transaction_id": "TXN-SLA",
        "confidence": Decimal("0.40"),
        "history_6m": (),
        "client_config": cfg,
    })
    assert d.decision == "PAUSE"

    # Simular que pasaron 3 horas (SLA es 2h)
    future = datetime.now(timezone.utc) + timedelta(hours=3)
    escalated = g.escalate_overdue(now=future)
    ok = d.pause_id in escalated
    pause = g.get_pause(d.pause_id)
    ok = ok and pause is not None and pause.escalated_to == "CPA-BACKUP"
    return _run("24", ok, f"escalated={escalated} escalated_to={pause.escalated_to if pause else None}")


def test_no_escalacion_sin_sla_vencido():
    """SLA no vencido → no se escala."""
    g = CentinelaGuardian()
    cfg = ClientConfig(
        client_id="CLI-NO-ESC",
        sla_hours=48,
        escalation_chain=("CPA-BACKUP",),
    )
    d = _base_evaluate(g, {
        "transaction_id": "TXN-NO-ESC",
        "confidence": Decimal("0.40"),
        "history_6m": (),
        "client_config": cfg,
    })
    # Solo pasaron 10 minutos
    soon = datetime.now(timezone.utc) + timedelta(minutes=10)
    escalated = g.escalate_overdue(now=soon)
    ok = len(escalated) == 0
    return _run("25", ok, f"escalated={escalated}")


# =========================================================================
# GRUPO 8 — Propiedades del sistema
# =========================================================================

def test_pause_log_inmutable():
    """El pause_log es append-only tuple."""
    g = CentinelaGuardian()
    for i in range(4):
        _base_evaluate(g, {
            "transaction_id": f"LOG{i}",
            "confidence": Decimal("0.30"),
            "history_6m": (),
        })
    log = g.get_pause_log()
    ok = len(log) == 4 and isinstance(log, tuple)
    return _run("26", ok, f"len={len(log)}")


def test_pausa_quirurgica_multiples_clientes():
    """Pausa de cliente A no afecta transacciones del cliente B."""
    g = CentinelaGuardian()
    # Pausa cliente A
    _base_evaluate(g, {
        "transaction_id": "TXN-CLIENTE-A",
        "client_id":      "CLI-A",
        "confidence":     Decimal("0.30"),
        "history_6m":     (),
    })
    # Cliente B tiene transacciones libres
    pausas_b = g.get_active_pauses_for_client("CLI-B")
    blocked_b = g.is_transaction_blocked("TXN-CLIENTE-B")
    ok = len(pausas_b) == 0 and not blocked_b
    return _run("27", ok, f"pausas_B={len(pausas_b)} blocked_B={blocked_b}")


def test_surgical_pause_model_frozen():
    """SurgicalPause es inmutable."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-FROZEN",
        "confidence": Decimal("0.40"),
        "history_6m": (),
    })
    pause = g.get_pause(d.pause_id)
    try:
        pause.status = "HACKED"  # type: ignore[misc]
        ok = False
    except Exception:
        ok = True
    return _run("28", ok)


def test_interpretaciones_incluidas_en_pausa():
    """Cada pausa incluye 2-3 interpretaciones para el CPA."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-INTERP",
        "confidence": Decimal("0.40"),
        "history_6m": (),
        "vendor":     "Consultores Nuevos SA",
    })
    pause = g.get_pause(d.pause_id)
    ok = pause is not None and len(pause.interpretations) >= 2
    return _run("29", ok, f"interpretaciones={len(pause.interpretations) if pause else 0}")


def test_impacto_fiscal_estimado():
    """Cada pausa incluye estimados de impacto fiscal."""
    g = CentinelaGuardian()
    d = _base_evaluate(g, {
        "transaction_id": "TXN-FISCAL",
        "confidence": Decimal("0.40"),
        "history_6m": (),
        "amount": Decimal("1000.00"),
    })
    pause = g.get_pause(d.pause_id)
    ok = pause is not None and len(pause.fiscal_impact) >= 1
    return _run("30", ok, f"fiscal_estimates={len(pause.fiscal_impact) if pause else 0}")


# =========================================================================
# RUNNER
# =========================================================================

ALL_TESTS = [
    ("01 Trigger1 sin contradiccion",            test_trigger1_sin_contradiccion),
    ("02 Trigger1 contradiccion SS 2024→2025",   test_trigger1_contradiccion_activa),
    ("03 Trigger1 pausa quirurgica",             test_trigger1_pausa_quirurgica),
    ("04 Trigger2 confidence sobre umbral",      test_trigger2_confidence_sobre_umbral),
    ("05 Trigger2 confidence bajo umbral",       test_trigger2_confidence_bajo_umbral_default),
    ("06 Trigger2 umbral personalizado alto",    test_trigger2_umbral_personalizado_alto),
    ("07 Trigger2 umbral personalizado bajo",    test_trigger2_umbral_personalizado_bajo),
    ("08 Trigger3 con precedente",               test_trigger3_con_precedente),
    ("09 Trigger3 sin precedente monto alto",    test_trigger3_sin_precedente_monto_alto),
    ("10 Trigger3 sin precedente monto bajo",    test_trigger3_sin_precedente_monto_bajo),
    ("11 Trigger4 agentes de acuerdo",           test_trigger4_agentes_de_acuerdo),
    ("12 Trigger4 CLASIFICADOR vs AUDITOR",      test_trigger4_clasificador_vs_auditor),
    ("13 Trigger4 divergencia fiscal",           test_trigger4_divergencia_fiscal),
    ("14 Trigger5 audit sin HIGH/CRITICAL",      test_trigger5_audit_pass),
    ("15 Trigger5 audit HIGH",                   test_trigger5_audit_high),
    ("16 Trigger5 audit CRITICAL balance roto",  test_trigger5_audit_critical),
    ("17 Trigger5 precede a otros triggers",     test_trigger5_precede_a_otros_triggers),
    ("18 Release correcta CPA",                  test_release_pause_correcta),
    ("19 Release sin licencia falla",            test_release_sin_licencia_falla),
    ("20 Release token corto falla",             test_release_token_corto_falla),
    ("21 Release sin instruccion falla",         test_release_sin_instruccion_falla),
    ("22 Release log inmutable",                 test_release_log_inmutable),
    ("23 Release ya resuelta falla",             test_release_ya_resuelta_falla),
    ("24 Escalacion por SLA vencido",            test_escalacion_por_sla),
    ("25 No escalacion SLA no vencido",          test_no_escalacion_sin_sla_vencido),
    ("26 Pause log inmutable",                   test_pause_log_inmutable),
    ("27 Pausa quirurgica multiples clientes",   test_pausa_quirurgica_multiples_clientes),
    ("28 SurgicalPause frozen",                  test_surgical_pause_model_frozen),
    ("29 Interpretaciones en pausa",             test_interpretaciones_incluidas_en_pausa),
    ("30 Impacto fiscal estimado",               test_impacto_fiscal_estimado),
]

if __name__ == "__main__":
    print("=" * 70)
    print("TESTS: CentinelaGuardian — Bit-Counting PR")
    print("=" * 70)

    passed = 0
    failed = 0

    for label, fn in ALL_TESTS:
        try:
            ok = fn()
            passed += 1 if ok else 0
            failed += 0 if ok else 1
        except Exception as exc:
            failed += 1
            print(f"  TEST {label}: EXCEPTION — {exc}")

    print("=" * 70)
    print(f"RESULTADO: {passed} passed, {failed} failed")
    if failed == 0:
        print("TODOS LOS TESTS PASARON.")
    print("=" * 70)
    sys.exit(0 if failed == 0 else 1)
