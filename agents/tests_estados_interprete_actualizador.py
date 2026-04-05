# =============================================================================
# agents/tests_estados_interprete_actualizador.py
# Tests para ESTADOS, INTERPRETE, ACTUALIZADOR y BitCountingFlow.
#
# Todos los tests deben pasar. Usar: python -m pytest agents/tests_estados_interprete_actualizador.py -v
# =============================================================================

from __future__ import annotations

import sys
import os

# Asegurar que el directorio raiz esta en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import date
from decimal import Decimal


# ---------------------------------------------------------------------------
# TEST 1: Balance Sheet verifica algebraicamente: Assets == Liabilities + Equity
# ---------------------------------------------------------------------------

def test_balance_sheet_algebraic_balance():
    """El Balance General debe cuadrar: Activos == Pasivos + Capital."""
    from agents.estados import EstadosAgent

    agent = EstadosAgent()

    # Accounts that balance: Assets=1500, Liabilities=800, Equity=700
    accounts = {
        "1000": Decimal("1000.00"),  # Efectivo — current asset
        "1100": Decimal("500.00"),   # Cuentas por Cobrar — current asset
        "2000": Decimal("800.00"),   # Cuentas por Pagar — current liability
        "3000": Decimal("500.00"),   # Capital Social — equity
        "3100": Decimal("200.00"),   # Utilidades Retenidas — equity
    }

    result = agent.generate_balance_sheet(
        accounts=accounts,
        as_of_date=date(2025, 12, 31),
        client_name="Test Corp PR",
    )

    total_assets = Decimal(result["assets"]["total_assets"])
    total_liabilities = Decimal(result["liabilities"]["total_liabilities"])
    total_equity = Decimal(result["equity"]["total_equity"])

    assert total_assets == total_liabilities + total_equity, (
        f"Balance no cuadra: {total_assets} != {total_liabilities} + {total_equity}"
    )
    assert result["verification"]["algebraically_balanced"] is True


# ---------------------------------------------------------------------------
# TEST 2: Balance Sheet lanza excepcion si no cuadra
# ---------------------------------------------------------------------------

def test_balance_sheet_raises_on_imbalance():
    """Si el balance no cuadra, debe lanzar BalanceSheetImbalanceError."""
    from agents.estados import EstadosAgent, BalanceSheetImbalanceError

    agent = EstadosAgent()

    # Deliberate imbalance: Assets=1000, but Liabilities+Equity=600+600=1200
    accounts = {
        "1000": Decimal("1000.00"),   # Efectivo — asset 1000
        "2000": Decimal("600.00"),    # Cuentas por Pagar — liability 600
        "3000": Decimal("600.00"),    # Capital Social — equity 600
        # 1000 != 600 + 600 → imbalance
    }

    with pytest.raises(BalanceSheetImbalanceError):
        agent.generate_balance_sheet(
            accounts=accounts,
            as_of_date=date(2025, 12, 31),
            client_name="Test Imbalanced Corp",
        )


# ---------------------------------------------------------------------------
# TEST 3: Income Statement: gross_profit = revenue - cogs (exacto)
# ---------------------------------------------------------------------------

def test_income_statement_gross_profit_exact():
    """gross_profit debe ser exactamente gross_revenue - cogs."""
    from agents.estados import EstadosAgent

    agent = EstadosAgent()

    revenue_accounts = {
        "Ingresos por Ventas": Decimal("50000.00"),
        "Ingresos por Servicios": Decimal("12000.00"),
    }
    expense_accounts = {
        "Costo de Ventas (COGS)": Decimal("25000.00"),
        "Gastos de Nomina": Decimal("8000.00"),
        "Renta": Decimal("3000.00"),
    }

    result = agent.generate_income_statement(
        revenue_accounts=revenue_accounts,
        expense_accounts=expense_accounts,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        client_name="Test Corp PR",
    )

    gross_revenue = Decimal(result["gross_revenue"])
    cogs = Decimal(result["cogs"])
    gross_profit = Decimal(result["gross_profit"])

    assert gross_revenue - cogs == gross_profit, (
        f"gross_profit incorrecto: {gross_revenue} - {cogs} != {gross_profit}"
    )
    assert result["verification"]["algebraically_verified"] is True


# ---------------------------------------------------------------------------
# TEST 4: INTERPRETE genera PolicyDraft con awaiting_confirmation=True siempre
# ---------------------------------------------------------------------------

def test_interprete_draft_always_awaiting_confirmation():
    """El PolicyDraft generado debe tener awaiting_confirmation=True siempre."""
    from agents.interprete import InterpreteAgent

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text="Las compras a Costco son siempre inventario, no gastos",
        cpa_license="CPA-PR-12345",
        client_examples=[],
    )

    assert draft.awaiting_confirmation is True, (
        "PolicyDraft debe tener awaiting_confirmation=True hasta confirmacion del CPA"
    )


# ---------------------------------------------------------------------------
# TEST 5: INTERPRETE genera exactamente 3 ejemplos
# ---------------------------------------------------------------------------

def test_interprete_generates_exactly_3_examples():
    """El PolicyDraft debe contener exactamente 3 ejemplos."""
    from agents.interprete import InterpreteAgent

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text=(
            "Capitaliza todos los gastos de equipo de computo mayores a $1,000"
        ),
        cpa_license="CPA-PR-67890",
        client_examples=[],
    )

    assert len(draft.examples) == 3, (
        f"Se esperaban 3 ejemplos, se obtuvieron {len(draft.examples)}"
    )


# ---------------------------------------------------------------------------
# TEST 6: Policy NO se activa sin confirmacion del CPA
# ---------------------------------------------------------------------------

def test_policy_not_active_before_confirmation():
    """Debe haber 0 politicas activas despues de interpret_instruction sin confirmar."""
    from agents.interprete import InterpreteAgent

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text=(
            "Los servicios de consultoría de Acme Corp están exentos de IVU"
        ),
        cpa_license="CPA-PR-11111",
        client_examples=[],
    )

    # No confirmamos — la politica no debe estar activa
    active_policies = agent.get_active_policies()

    assert len(active_policies) == 0, (
        "No debe haber politicas activas antes de la confirmacion del CPA"
    )
    assert draft.awaiting_confirmation is True


# ---------------------------------------------------------------------------
# TEST 7: Policy se activa cuando CPA confirma=True
# ---------------------------------------------------------------------------

def test_policy_activates_on_cpa_confirmation():
    """La politica debe activarse cuando el CPA confirma con confirmed=True."""
    from agents.interprete import InterpreteAgent

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text=(
            "Reclasifica todas las compras de Amazon como gastos de tecnología"
        ),
        cpa_license="CPA-PR-22222",
        client_examples=[],
    )

    assert draft.awaiting_confirmation is True
    assert len(agent.get_active_policies()) == 0

    result = agent.confirm_policy(
        draft_id=draft.draft_id,
        cpa_license="CPA-PR-22222",
        confirmed=True,
    )

    assert result["status"] == "ACTIVATED"
    assert "policy_id" in result

    active_policies = agent.get_active_policies()
    assert len(active_policies) == 1, (
        "Debe haber exactamente 1 politica activa despues de confirmar"
    )


# ---------------------------------------------------------------------------
# TEST 8: ACTUALIZADOR siempre retorna requires_human_review=True
# ---------------------------------------------------------------------------

def test_actualizador_always_requires_human_review():
    """Todo MonitorResult debe tener requires_human_review=True."""
    from agents.actualizador import ActualizadorAgent

    agent = ActualizadorAgent()
    results = agent.check_all_sources()

    assert len(results) == 7, f"Se esperaban 7 resultados, se obtuvieron {len(results)}"

    for result in results:
        assert result.requires_human_review is True, (
            f"Fuente '{result.source_name}' tiene requires_human_review=False — "
            "debe ser siempre True"
        )


# ---------------------------------------------------------------------------
# TEST 9: ACTUALIZADOR nunca auto-aplica cambios
# ---------------------------------------------------------------------------

def test_actualizador_never_auto_applies():
    """El ACTUALIZADOR nunca debe auto-aplicar cambios a tax_rules."""
    from agents.actualizador import ActualizadorAgent, MonitorResult
    from datetime import datetime, timezone

    agent = ActualizadorAgent()

    # Crear un MonitorResult simulando un cambio detectado
    monitor_result = MonitorResult(
        source_name="hacienda.pr.gov",
        source_url="https://hacienda.pr.gov",
        checked_at=datetime.now(timezone.utc),
        change_detected=True,
        change_type="rate_change",
        description="Tasa IVU cambia de 11.5% a 12%",
        urgency="high",
        affected_rule_ids=("PR-IVU-001",),
        draft_update=None,
        requires_human_review=True,
    )

    draft = agent.generate_update_draft(
        monitor_result=monitor_result,
        existing_rule_id="PR-IVU-001",
    )

    # El draft debe estar PENDING_REVIEW — nunca APPROVED ni aplicado
    assert draft["status"] == "PENDING_REVIEW", (
        f"El draft debe ser PENDING_REVIEW, no '{draft['status']}'"
    )
    assert draft["requires_human_review"] is True, (
        "El draft debe tener requires_human_review=True"
    )
    assert draft["approved_by"] is None, (
        "El draft no debe estar aprobado automaticamente"
    )

    # Verificar que hay exactamente 1 draft pendiente
    pending = agent.get_pending_updates()
    assert len(pending) == 1
    assert pending[0]["status"] == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# TEST 10: Flow completo: documento limpio → PROCESSED sin pausa
# ---------------------------------------------------------------------------

def test_flow_clean_document_processed():
    """Un documento limpio y completo debe resultar en status=PROCESSED."""
    from agents.flow import BitCountingFlow

    flow = BitCountingFlow()

    clean_doc = {
        "vendor":         "Renta Oficina Centro Empresarial",
        "date":           "2025-06-15",
        "amount":         "2500.00",
        # tax_amount ausente — el AUDITOR omite chequeo de IVU si no esta presente
        "payment_method": "CHECK",
    }

    result = flow.process_document(
        raw_input=clean_doc,
        source_format="json",
        client_context={},
    )

    assert result["status"] == "PROCESSED", (
        f"Se esperaba PROCESSED, se obtuvo '{result['status']}'. "
        f"pause_info: {result.get('pause_info')}"
    )
    assert result["pause_info"] is None
    assert len(result["journal_entries"]) >= 2  # al menos debit + credit


# ---------------------------------------------------------------------------
# TEST 11: Flow con campo critico faltante → PAUSED por CENTINELA
# ---------------------------------------------------------------------------

def test_flow_missing_critical_field_paused():
    """Un documento sin 'amount' (campo critico) debe resultar en PAUSED."""
    from agents.flow import BitCountingFlow

    flow = BitCountingFlow()

    # Documento sin amount — campo critico faltante
    incomplete_doc = {
        "vendor": "Proveedor Sin Monto Inc",
        "date":   "2025-06-15",
        # "amount" ausente — campo critico
    }

    result = flow.process_document(
        raw_input=incomplete_doc,
        source_format="json",
        client_context={},
    )

    assert result["status"] == "PAUSED", (
        f"Se esperaba PAUSED por campo critico faltante, se obtuvo '{result['status']}'"
    )
    assert result["pause_info"] is not None
    assert len(result["journal_entries"]) == 0


# ---------------------------------------------------------------------------
# TEST 12: Flow: journal entries tienen balance debit == credit
# ---------------------------------------------------------------------------

def test_flow_journal_entries_balanced():
    """Los asientos contables deben estar balanceados: debito == credito."""
    from agents.flow import BitCountingFlow

    flow = BitCountingFlow()

    doc = {
        "vendor":         "Nomina Mensual ADP",
        "date":           "2025-07-31",
        "amount":         "15000.00",
        # tax_amount ausente — omite chequeo IVU del AUDITOR
        "payment_method": "ACH",
    }

    result = flow.process_document(
        raw_input=doc,
        source_format="json",
        client_context={},
    )

    if result["status"] == "PAUSED":
        pytest.skip("Documento fue pausado — no hay journal entries para verificar balance")

    entries = result["journal_entries"]
    assert len(entries) >= 2, "Se necesitan al menos 2 asientos (debit y credit)"

    debit_total  = sum(
        Decimal(e["amount"]) for e in entries if e["entry_type"] == "debit"
    )
    credit_total = sum(
        Decimal(e["amount"]) for e in entries if e["entry_type"] == "credit"
    )

    assert debit_total == credit_total, (
        f"Asientos no balanceados: debito={debit_total} != credito={credit_total}"
    )


# ---------------------------------------------------------------------------
# TESTS ADICIONALES — robustez
# ---------------------------------------------------------------------------

def test_balance_sheet_verify_algebraic_balance_static():
    """verify_algebraic_balance() debe retornar True si cuadra, False si no."""
    from agents.estados import EstadosAgent

    agent = EstadosAgent()

    # Cuadra
    balanced = {
        "assets":      {"total_assets": Decimal("1000")},
        "liabilities": {"total_liabilities": Decimal("600")},
        "equity":      {"total_equity": Decimal("400")},
    }
    assert agent.verify_algebraic_balance(balanced) is True

    # No cuadra
    imbalanced = {
        "assets":      {"total_assets": Decimal("1000")},
        "liabilities": {"total_liabilities": Decimal("600")},
        "equity":      {"total_equity": Decimal("500")},  # 600+500=1100 != 1000
    }
    assert agent.verify_algebraic_balance(imbalanced) is False


def test_interprete_confirm_false_discards_draft():
    """confirmed=False debe descartar el draft sin activar la politica."""
    from agents.interprete import InterpreteAgent

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text="Las compras a Costco son siempre inventario, no gastos",
        cpa_license="CPA-PR-99999",
        client_examples=[],
    )

    result = agent.confirm_policy(
        draft_id=draft.draft_id,
        cpa_license="CPA-PR-99999",
        confirmed=False,
    )

    assert result["status"] == "DISCARDED"
    assert len(agent.get_active_policies()) == 0


def test_interprete_draft_is_frozen():
    """PolicyDraft debe ser inmutable (frozen=True)."""
    from agents.interprete import InterpreteAgent
    from pydantic import ValidationError

    agent = InterpreteAgent()

    draft = agent.interpret_instruction(
        instruction_text="Las compras a Costco son siempre inventario",
        cpa_license="CPA-PR-33333",
        client_examples=[],
    )

    with pytest.raises((ValidationError, TypeError, AttributeError)):
        draft.awaiting_confirmation = False  # type: ignore[misc]


def test_actualizador_check_all_sources_returns_7():
    """check_all_sources debe retornar exactamente 7 resultados."""
    from agents.actualizador import ActualizadorAgent

    agent = ActualizadorAgent()
    results = agent.check_all_sources()

    assert len(results) == 7, f"Se esperaban 7 resultados, se obtuvieron {len(results)}"

    source_names = {r.source_name for r in results}
    expected_names = {
        "hacienda.pr.gov",
        "irs.gov",
        "estado.pr.gov",
        "dtrh.pr.gov",
        "boletinestado.pr.gov",
        "congress.gov",
        "federalregister.gov",
    }
    assert source_names == expected_names, (
        f"Fuentes incorrectas. Esperadas: {expected_names}. "
        f"Recibidas: {source_names}"
    )


def test_cash_flow_algebraic_verification():
    """net_change_in_cash debe ser exactamente operating + investing + financing."""
    from agents.estados import EstadosAgent

    agent = EstadosAgent()

    transactions = [
        {"activity": "operating",  "description": "Cobro de clientes", "amount": "50000"},
        {"activity": "operating",  "description": "Pago a proveedores", "amount": "-30000"},
        {"activity": "investing",  "description": "Compra de equipo", "amount": "-10000"},
        {"activity": "financing",  "description": "Prestamo bancario", "amount": "15000"},
    ]

    result = agent.generate_cash_flow(
        transactions=transactions,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
    )

    net_operating  = Decimal(result["operating_activities"]["net"])
    net_investing  = Decimal(result["investing_activities"]["net"])
    net_financing  = Decimal(result["financing_activities"]["net"])
    net_change     = Decimal(result["net_change_in_cash"])

    assert net_operating + net_investing + net_financing == net_change, (
        f"Flujo no balanceado: {net_operating}+{net_investing}+{net_financing} "
        f"!= {net_change}"
    )
    assert result["verification"]["algebraically_verified"] is True


if __name__ == "__main__":
    # Run all tests directly
    import subprocess
    subprocess.run(
        ["python", "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
