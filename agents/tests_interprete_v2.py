# =============================================================================
# agents/tests_interprete_v2.py
# 30 tests para InterpreteV2 — flujo CONFIRMAR/CORREGIR completo.
# =============================================================================

from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal

from agents.interprete_v2 import (
    InterpreteV2,
    PolicyScope,
    PolicyStatus,
    PolicyDraftV2,
    ActivePolicyV2,
    ContradictionAlert,
    CentinelaNotification,
    CorrectionCycle,
    TransactionExample,
)
from agents.exceptions import BitCountingAgentError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def interp() -> InterpreteV2:
    return InterpreteV2()


INST_ALQUILER = (
    "Todas las facturas de alquiler de oficina deben clasificarse "
    "en la cuenta Gasto de Arrendamiento a partir de enero 2025."
)
INST_NOMINA = (
    "Los pagos de nomina y salarios deben separarse por departamento "
    "y clasificarse segun el centro de costo."
)
INST_IVU = (
    "Registrar el IVU pagado en compras como cuenta separada "
    "IVU Pagado — no mezclarlo con el gasto principal."
)
CPA = "CPA-PR-12345"
CLIENT = "CLI-001"


# ---------------------------------------------------------------------------
# 1. Happy path basico
# ---------------------------------------------------------------------------

def test_interpret_basic_instruction(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    assert isinstance(draft, PolicyDraftV2)
    assert draft.status == PolicyStatus.AWAITING_CONFIRMATION
    assert len(draft.transaction_examples) == 3
    assert draft.cpa_license == CPA


# ---------------------------------------------------------------------------
# 2. effective_date por defecto es hoy
# ---------------------------------------------------------------------------

def test_interpret_default_effective_date(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    assert draft.effective_date == date.today().isoformat()


# ---------------------------------------------------------------------------
# 3. SINGLE_CLIENT sin client_id lanza error
# ---------------------------------------------------------------------------

def test_interpret_single_client_requires_client_id(interp):
    with pytest.raises(BitCountingAgentError, match="client_id"):
        interp.interpret_instruction(
            INST_ALQUILER, CPA,
            scope=PolicyScope.SINGLE_CLIENT,
            # client_id omitido
        )


# ---------------------------------------------------------------------------
# 4. ALL_CPA_CLIENTS no requiere client_id
# ---------------------------------------------------------------------------

def test_interpret_all_cpa_clients_no_client_id_required(interp):
    draft = interp.interpret_instruction(
        INST_ALQUILER, CPA,
        scope=PolicyScope.ALL_CPA_CLIENTS,
    )
    assert draft.scope == PolicyScope.ALL_CPA_CLIENTS
    assert draft.client_id is None


# ---------------------------------------------------------------------------
# 5. EXIMIA_POOL activa requires_eximia_approval
# ---------------------------------------------------------------------------

def test_interpret_eximia_sets_requires_approval(interp):
    draft = interp.interpret_instruction(
        INST_ALQUILER, CPA,
        scope=PolicyScope.EXIMIA_POOL,
    )
    assert draft.requires_eximia_approval is True


# ---------------------------------------------------------------------------
# 6. Keyword "alquiler" → "RENT"
# ---------------------------------------------------------------------------

def test_interpret_keyword_detection_alquiler(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    assert "RENT" in draft.affected_transaction_types


# ---------------------------------------------------------------------------
# 7. Keyword "nomina" → "PAYROLL"
# ---------------------------------------------------------------------------

def test_interpret_keyword_detection_nomina(interp):
    draft = interp.interpret_instruction(INST_NOMINA, CPA, client_id=CLIENT)
    assert "PAYROLL" in draft.affected_transaction_types


# ---------------------------------------------------------------------------
# 8. Keyword "ivu" → "TAX_IVU"
# ---------------------------------------------------------------------------

def test_interpret_keyword_detection_ivu(interp):
    draft = interp.interpret_instruction(INST_IVU, CPA, client_id=CLIENT)
    assert "TAX_IVU" in draft.affected_transaction_types


# ---------------------------------------------------------------------------
# 9. PolicyDraftV2 es inmutable (frozen)
# ---------------------------------------------------------------------------

def test_draft_is_frozen(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    with pytest.raises(Exception):
        draft.status = PolicyStatus.ACTIVE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. corregir crea nuevo ciclo en correction_history
# ---------------------------------------------------------------------------

def test_corregir_creates_new_cycle(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    updated = interp.corregir(draft.draft_id, "Incluir tambien alquileres de depositos", CPA)
    assert len(updated.correction_history) == 1
    assert isinstance(updated.correction_history[0], CorrectionCycle)


# ---------------------------------------------------------------------------
# 11. corregir incorpora la nota en la instruccion actualizada
# ---------------------------------------------------------------------------

def test_corregir_updates_instruction(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    nota = "Incluir tambien alquileres de depositos"
    updated = interp.corregir(draft.draft_id, nota, CPA)
    assert nota in updated.instruction_text
    assert "[CORRECCION 1]" in updated.instruction_text


# ---------------------------------------------------------------------------
# 12. corregir con CPA equivocado lanza error
# ---------------------------------------------------------------------------

def test_corregir_wrong_cpa_raises(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    with pytest.raises(BitCountingAgentError, match="license"):
        interp.corregir(draft.draft_id, "nota", "CPA-FALSO-9999")


# ---------------------------------------------------------------------------
# 13. corregir con draft_id inexistente lanza error
# ---------------------------------------------------------------------------

def test_corregir_nonexistent_draft_raises(interp):
    with pytest.raises(BitCountingAgentError, match="Draft no encontrado"):
        interp.corregir("00000000-0000-0000-0000-000000000000", "nota", CPA)


# ---------------------------------------------------------------------------
# 14. corregir sobre draft ya confirmado lanza error
# ---------------------------------------------------------------------------

def test_corregir_already_active_raises(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(draft.draft_id, CPA)
    with pytest.raises(BitCountingAgentError, match="AWAITING_CONFIRMATION"):
        interp.corregir(draft.draft_id, "demasiado tarde", CPA)


# ---------------------------------------------------------------------------
# 15. Multiples ciclos CORREGIR — correction_history crece
# ---------------------------------------------------------------------------

def test_multiple_corregir_cycles(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    d1 = interp.corregir(draft.draft_id, "Primera correccion", CPA)
    d2 = interp.corregir(draft.draft_id, "Segunda correccion", CPA)
    d3 = interp.corregir(draft.draft_id, "Tercera correccion", CPA)
    assert len(d3.correction_history) == 3
    assert d3.correction_history[0].cycle_number == 1
    assert d3.correction_history[1].cycle_number == 2
    assert d3.correction_history[2].cycle_number == 3


# ---------------------------------------------------------------------------
# 16. confirmar retorna ActivePolicyV2 con status ACTIVE
# ---------------------------------------------------------------------------

def test_confirmar_happy_path(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    active = interp.confirmar(draft.draft_id, CPA)
    assert isinstance(active, ActivePolicyV2)
    assert active.status == PolicyStatus.ACTIVE
    assert active.draft_id == draft.draft_id


# ---------------------------------------------------------------------------
# 17. correction_cycles refleja el numero de CORREGIR previos
# ---------------------------------------------------------------------------

def test_confirmar_sets_correct_correction_cycles(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.corregir(draft.draft_id, "Primera", CPA)
    interp.corregir(draft.draft_id, "Segunda", CPA)
    active = interp.confirmar(draft.draft_id, CPA)
    assert active.correction_cycles == 2


# ---------------------------------------------------------------------------
# 18. confirmar EXIMIA_POOL sin token lanza error
# ---------------------------------------------------------------------------

def test_confirmar_eximia_requires_token(interp):
    draft = interp.interpret_instruction(
        INST_ALQUILER, CPA, scope=PolicyScope.EXIMIA_POOL
    )
    with pytest.raises(BitCountingAgentError, match="eximia_approval_token"):
        interp.confirmar(draft.draft_id, CPA)


# ---------------------------------------------------------------------------
# 19. confirmar EXIMIA_POOL con token corto lanza error
# ---------------------------------------------------------------------------

def test_confirmar_eximia_token_too_short(interp):
    draft = interp.interpret_instruction(
        INST_ALQUILER, CPA, scope=PolicyScope.EXIMIA_POOL
    )
    with pytest.raises(BitCountingAgentError, match="corto"):
        interp.confirmar(draft.draft_id, CPA, eximia_approval_token="SHORT")


# ---------------------------------------------------------------------------
# 20. confirmar EXIMIA_POOL con token valido funciona
# ---------------------------------------------------------------------------

def test_confirmar_eximia_with_valid_token(interp):
    draft = interp.interpret_instruction(
        INST_ALQUILER, CPA, scope=PolicyScope.EXIMIA_POOL
    )
    active = interp.confirmar(draft.draft_id, CPA, eximia_approval_token="TOKEN-EXIMIA-2025")
    assert active.status == PolicyStatus.ACTIVE
    assert active.scope == PolicyScope.EXIMIA_POOL


# ---------------------------------------------------------------------------
# 21. Despues de confirmar, el draft tiene status ACTIVE
# ---------------------------------------------------------------------------

def test_confirmar_draft_becomes_active_in_drafts(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(draft.draft_id, CPA)
    # get_drafts_awaiting_confirmation ya no debe incluirlo
    pending = interp.get_drafts_awaiting_confirmation(CPA)
    assert all(d.draft_id != draft.draft_id for d in pending)


# ---------------------------------------------------------------------------
# 22. Politica activa aparece en get_active_policies()
# ---------------------------------------------------------------------------

def test_confirmar_policy_in_active_list(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    active = interp.confirmar(draft.draft_id, CPA)
    policies = interp.get_active_policies()
    assert any(p.policy_id == active.policy_id for p in policies)


# ---------------------------------------------------------------------------
# 23. get_active_policies filtra por client_id (+ ALL_CPA + EXIMIA incluidos)
# ---------------------------------------------------------------------------

def test_get_active_policies_filter_by_client(interp):
    # Politica para cliente A
    d1 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id="CLI-A")
    interp.confirmar(d1.draft_id, CPA)

    # Politica para cliente B
    d2 = interp.interpret_instruction(INST_NOMINA, CPA, client_id="CLI-B")
    interp.confirmar(d2.draft_id, CPA)

    # Politica ALL_CPA_CLIENTS
    d3 = interp.interpret_instruction(INST_IVU, CPA, scope=PolicyScope.ALL_CPA_CLIENTS)
    p3 = interp.confirmar(d3.draft_id, CPA)

    result_a = interp.get_active_policies(client_id="CLI-A")
    policy_ids = [p.policy_id for p in result_a]

    # debe incluir la de CLI-A y la de ALL_CPA_CLIENTS, pero NO la de CLI-B
    assert any(p.client_id == "CLI-A" for p in result_a)
    assert p3.policy_id in policy_ids
    assert all(p.client_id != "CLI-B" for p in result_a if p.scope == PolicyScope.SINGLE_CLIENT)


# ---------------------------------------------------------------------------
# 24. Contradiccion detectada: mismo tipo, mismo cliente
# ---------------------------------------------------------------------------

def test_contradiction_detection_same_type_same_client(interp):
    d1 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(d1.draft_id, CPA)

    d2 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(d2.draft_id, CPA)

    alerts = interp.get_contradiction_alerts()
    assert len(alerts) >= 1
    assert all(a.resolution == "NEW_POLICY_WINS_FUTURE" for a in alerts)


# ---------------------------------------------------------------------------
# 25. Sin superposicion de tipos → sin contradiccion
# ---------------------------------------------------------------------------

def test_contradiction_detection_no_overlap_no_alert(interp):
    d1 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(d1.draft_id, CPA)

    # Instruccion sobre nomina (tipo diferente: PAYROLL vs RENT)
    d2 = interp.interpret_instruction(INST_NOMINA, CPA, client_id=CLIENT)
    interp.confirmar(d2.draft_id, CPA)

    alerts = interp.get_contradiction_alerts()
    # No debe haber contradiccion entre RENT y PAYROLL
    assert len(alerts) == 0


# ---------------------------------------------------------------------------
# 26. Politica contradictoria queda como SUPERSEDED
# ---------------------------------------------------------------------------

def test_contradiction_older_policy_superseded(interp):
    d1 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    p1 = interp.confirmar(d1.draft_id, CPA)

    d2 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    interp.confirmar(d2.draft_id, CPA)

    all_policies = interp._active_policies
    old = next(p for p in all_policies if p.policy_id == p1.policy_id)
    assert old.status == PolicyStatus.SUPERSEDED
    assert old.superseded_by is not None


# ---------------------------------------------------------------------------
# 27. CENTINELA recibe notificacion al confirmar
# ---------------------------------------------------------------------------

def test_centinela_notification_sent_on_confirm(interp):
    draft = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    active = interp.confirmar(draft.draft_id, CPA)

    notifications = interp.get_centinela_notifications()
    assert len(notifications) == 1
    assert notifications[0].policy_id == active.policy_id
    assert notifications[0].event_type == "POLICY_ACTIVATED"


# ---------------------------------------------------------------------------
# 28. get_drafts_awaiting_confirmation retorna solo los pendientes del CPA
# ---------------------------------------------------------------------------

def test_get_drafts_awaiting_confirmation(interp):
    d1 = interp.interpret_instruction(INST_ALQUILER, CPA, client_id=CLIENT)
    d2 = interp.interpret_instruction(INST_NOMINA, CPA, client_id=CLIENT)
    interp.confirmar(d1.draft_id, CPA)  # confirmar el primero

    pending = interp.get_drafts_awaiting_confirmation(CPA)
    draft_ids = [d.draft_id for d in pending]
    assert d2.draft_id in draft_ids
    assert d1.draft_id not in draft_ids


# ---------------------------------------------------------------------------
# 29. get_contradiction_alerts retorna tuple (inmutable)
# ---------------------------------------------------------------------------

def test_get_contradiction_alerts_immutable(interp):
    alerts = interp.get_contradiction_alerts()
    assert isinstance(alerts, tuple)


# ---------------------------------------------------------------------------
# 30. Flujo completo: interpret → corregir → corregir → confirmar
# ---------------------------------------------------------------------------

def test_full_corregir_confirmar_flow(interp):
    # Paso 1: interpretar
    draft = interp.interpret_instruction(
        "Clasificar alquiler de oficina principal en Santurce",
        CPA,
        client_id="CLI-SANTURCE",
        effective_date="2025-03-01",
    )
    assert draft.status == PolicyStatus.AWAITING_CONFIRMATION
    assert len(draft.correction_history) == 0

    # Paso 2: primera correccion
    d1 = interp.corregir(draft.draft_id, "Incluir deposito de garantia tambien", CPA)
    assert len(d1.correction_history) == 1
    assert d1.correction_history[0].cycle_number == 1

    # Paso 3: segunda correccion
    d2 = interp.corregir(draft.draft_id, "Aplicar desde enero 2025 no marzo", CPA)
    assert len(d2.correction_history) == 2
    assert d2.correction_history[1].cycle_number == 2

    # Paso 4: confirmar
    active = interp.confirmar(draft.draft_id, CPA)

    assert isinstance(active, ActivePolicyV2)
    assert active.status == PolicyStatus.ACTIVE
    assert active.correction_cycles == 2
    assert active.effective_date == "2025-03-01"
    assert active.client_id == "CLI-SANTURCE"

    # Politica aparece en lista activa
    policies = interp.get_active_policies(client_id="CLI-SANTURCE")
    assert any(p.policy_id == active.policy_id for p in policies)

    # CENTINELA fue notificado
    notifs = interp.get_centinela_notifications()
    assert len(notifs) == 1
    assert notifs[0].policy_id == active.policy_id
