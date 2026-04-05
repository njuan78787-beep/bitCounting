# =============================================================================
# agents/tests.py
# Tests del framework de agentes Bit-Counting.
# Ejecutar con: python -m agents.tests
# =============================================================================

import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

sys.path.insert(0, "/home/user/bitCounting")

from agents import (
    Orchestrator, Centinela, Intake,
    IntakeOutput, CentinelaDecision, CentinelaPause, PauseStatus,
    AgentScopeError, CentinelaBlockError, ImmutableLogError,
)
from agents.exceptions import IntakeValidationError

PASS = "[PASS]"
FAIL = "[FAIL]"

def test(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    return condition

def run_all():
    errors = 0

    # -----------------------------------------------------------------------
    print("\n=== TEST: IntakeAgent — extraccion de campos ===")

    intake = Intake()

    # Documento completo
    doc_completo = {
        "vendor": "Supermercados Pueblo",
        "date": "2025-03-15",
        "amount": "245.67",
        "tax_amount": "25.79",
        "currency": "USD",
        "payment_method": "credit_card",
        "line_items": [{"description": "Compras", "amount": "245.67"}],
    }
    out = intake.process_document(doc_completo, source_format="manual")
    if not test("Extraccion completa — no missing_fields",
                len(out.missing_fields) == 0, str(out.missing_fields)): errors += 1
    if not test("Amount extraido como Decimal",
                out.amount == Decimal("245.67"), str(out.amount)): errors += 1
    if not test("Date extraida correctamente",
                out.date == '2025-03-15', str(out.date)): errors += 1
    # "manual" mapea a SourceFormat.UNKNOWN (base 0.65) — confidence OK si no hay missing
    if not test("Confidence >= 0.60 para doc completo sin missing",
                out.confidence >= Decimal("0.60"), str(out.confidence)): errors += 1
    if not test("critical_fields_missing vacio",
                out.critical_fields_missing == False): errors += 1

    # Documento sin campos criticos
    doc_incompleto = {"vendor": "Tienda XYZ"}
    out2 = intake.process_document(doc_incompleto, source_format="manual")
    if not test("Campos criticos faltantes detectados (critical_fields_missing=True)",
                out2.critical_fields_missing == True,
                str(out2.critical_fields_missing)): errors += 1
    if not test("Missing fields incluye amount y date",
                "amount" in out2.missing_fields and "date" in out2.missing_fields,
                str(out2.missing_fields)): errors += 1
    if not test("Confidence baja por campos criticos faltantes",
                out2.confidence < Decimal("0.60"), str(out2.confidence)): errors += 1

    # Documento con formato monetario con signo $
    doc_formato = {"amount": "$1,234.56", "date": "01/15/2025"}
    out3 = intake.process_document(doc_formato, source_format="manual")
    if not test("Monto con $ y coma parseado correctamente",
                out3.amount == Decimal("1234.56"), str(out3.amount)): errors += 1

    # OCR de texto libre
    raw_text = """Supermercados Econo
    15/03/2025
    Total: $89.50
    IVU: $9.40
    """
    out_ocr = intake.process_document({"raw_text": raw_text}, source_format="ocr_photo")
    if not test("OCR extrae amount del texto",
                out_ocr.amount is not None, str(out_ocr.amount)): errors += 1
    if not test("OCR extrae date del texto",
                out_ocr.date is not None, str(out_ocr.date)): errors += 1
    if not test("Source format ocr_photo baja confidence base",
                out_ocr.confidence <= Decimal("0.75"), str(out_ocr.confidence)): errors += 1

    # IntakeOutput es frozen
    mutation_blocked = False
    try:
        out.vendor = "MODIFICADO"
    except Exception:
        mutation_blocked = True
    if not test("IntakeOutput es frozen — mutation bloqueada", mutation_blocked): errors += 1

    # -----------------------------------------------------------------------
    print("\n=== TEST: Centinela — evaluacion de documentos ===")

    centinela = Centinela()

    def make_intake_output(confidence: Decimal, critical_missing=()) -> IntakeOutput:
        from agents.messages import SourceFormat
        return IntakeOutput(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent="INTAKE",
            target_agent="CENTINELA",
            vendor="Test Vendor",
            date="2025-03-15",
            amount=Decimal("500.00") if not critical_missing else None,
            tax_amount=Decimal("52.50"),
            currency="USD",
            line_items=(),
            payment_method="cash",
            confidence=confidence,
            missing_fields=tuple(critical_missing),
            critical_fields_missing=bool(critical_missing),
            source_format=SourceFormat.UNKNOWN,
            raw_text_excerpt=None,
        )

    # Confidence alta — debe PROCEDER
    out_high = make_intake_output(Decimal("0.96"))
    eval_high = centinela.evaluate(out_high, client_history=(), active_rules=())
    if not test("Confidence 0.96 → CENTINELA PROCEED",
                eval_high.decision == CentinelaDecision.PROCEED,
                eval_high.decision): errors += 1

    # Confidence baja — debe PAUSAR
    out_low = make_intake_output(Decimal("0.55"))
    eval_low = centinela.evaluate(out_low, client_history=(), active_rules=())
    if not test("Confidence 0.55 → CENTINELA PAUSE",
                eval_low.decision == CentinelaDecision.PAUSE,
                eval_low.decision): errors += 1

    # Campos criticos faltantes — debe PAUSAR independiente del confidence
    out_critical = make_intake_output(Decimal("0.90"), critical_missing=["amount"])
    eval_critical = centinela.evaluate(out_critical, client_history=(), active_rules=())
    if not test("Campos criticos faltantes → CENTINELA PAUSE (sin importar confidence)",
                eval_critical.decision == CentinelaDecision.PAUSE,
                eval_critical.decision): errors += 1

    # -----------------------------------------------------------------------
    print("\n=== TEST: Centinela — pausas irrevocables ===")

    out_pause_input = make_intake_output(Decimal("0.40"))
    eval_result = centinela.evaluate(out_pause_input, client_history=(), active_rules=())
    if not test("Evaluacion con confidence bajo emite pause_id",
                eval_result.pause_id is not None, str(eval_result.pause_id)): errors += 1

    if eval_result.pause_id:
        pause_id = eval_result.pause_id
        active = centinela.get_active_pauses()
        if not test("Pausa aparece en get_active_pauses()",
                    any(p.pause_id == pause_id for p in active),
                    f"pauses={[p.pause_id for p in active]}"): errors += 1

        # Intento de liberar pausa sin token CPA — debe fallar
        release_blocked = False
        try:
            centinela.release_pause(pause_id, cpa_license="CPA-TEST-001", cpa_token="")
        except Exception:
            release_blocked = True
        if not test("Pausa NO se libera con token invalido", release_blocked): errors += 1

        # Pausa sigue activa despues del intento fallido
        active_after = centinela.get_active_pauses()
        if not test("Pausa sigue ACTIVE despues de intento fallido",
                    any(p.pause_id == pause_id and p.status == PauseStatus.ACTIVE
                        for p in active_after)): errors += 1

    # -----------------------------------------------------------------------
    print("\n=== TEST: Orchestrator — log append-only ===")

    orch = Orchestrator()

    # Log empieza vacio
    if not test("Log empieza vacio", len(orch.get_decision_log()) == 0): errors += 1

    # Loguear una decision
    orch.record_raw(
        agent_name="INTAKE",
        input_snapshot={"source": "test"},
        output_snapshot={"amount": "100.00"},
        rule_ids_applied=[],
        confidence=0.95,
    )
    if not test("Despues de 1 log, len=1", len(orch.get_decision_log()) == 1): errors += 1

    orch.record_raw(
        agent_name="CENTINELA",
        input_snapshot={"confidence": "0.95"},
        output_snapshot={"decision": "PROCEED"},
        rule_ids_applied=["IVU_ESTATAL_PR_2015_V1"],
        confidence=0.95,
    )
    if not test("Despues de 2 logs, len=2", len(orch.get_decision_log()) == 2): errors += 1

    # get_decision_log() retorna tuple (inmutable)
    log = orch.get_decision_log()
    if not test("get_decision_log() retorna tuple", isinstance(log, tuple)): errors += 1

    # No se puede modificar el tuple
    mutation_log_blocked = False
    try:
        log[0] = None  # type: ignore
    except TypeError:
        mutation_log_blocked = True
    if not test("Log tuple no puede ser modificado", mutation_log_blocked): errors += 1

    # ImmutableLogError al intentar operaciones prohibidas
    immutable_blocked = False
    try:
        orch.delete_decision("fake-id")
    except (ImmutableLogError, AttributeError):
        immutable_blocked = True
    if not test("delete_decision() no existe o lanza ImmutableLogError", immutable_blocked): errors += 1

    # Timestamps en orden cronologico (append-only implica orden)
    log2 = orch.get_decision_log()
    if len(log2) >= 2:
        if not test("Decisiones en orden cronologico (append-only)",
                    log2[0].timestamp <= log2[1].timestamp): errors += 1

    # -----------------------------------------------------------------------
    print("\n=== TEST: Flujo integrado INTAKE → CENTINELA → ORQUESTADOR ===")

    orch2 = Orchestrator()
    intake2 = Intake()
    centinela2 = Centinela()

    doc = {
        "vendor": "Costco Wholesale PR",
        "date": "2025-03-20",
        "amount": "1250.00",
        "tax_amount": "131.25",
        "currency": "USD",
    }

    # INTAKE procesa el documento
    intake_out = intake2.process_document(doc, source_format="manual", orchestrator=orch2)
    if not test("INTAKE produce IntakeOutput valido", isinstance(intake_out, IntakeOutput)): errors += 1
    if not test("INTAKE loguea en ORQUESTADOR", len(orch2.get_decision_log()) == 1): errors += 1

    # CENTINELA evalua
    centinela_eval = centinela2.evaluate(intake_out, client_history=(), active_rules=())
    if not test("Documento completo → CENTINELA PROCEED",
                centinela_eval.decision == CentinelaDecision.PROCEED,
                centinela_eval.decision): errors += 1

    # Log de ORQUESTADOR despues de INTAKE
    final_log = orch2.get_decision_log()
    if not test("Log final tiene al menos 1 entrada (INTAKE)", len(final_log) >= 1): errors += 1
    if not test("Primera entrada del log es del INTAKE",
                final_log[0].agent_name == "INTAKE"): errors += 1

    # -----------------------------------------------------------------------
    print(f"\n{'='*55}")
    total = 33
    passed = total - errors
    print(f"RESULTADO: {passed}/{total} tests pasados — {errors} fallidos")
    if errors == 0:
        print("TODOS LOS TESTS PASARON.")
    else:
        print(f"ATENCION: {errors} tests fallaron.")
    return errors == 0

if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
