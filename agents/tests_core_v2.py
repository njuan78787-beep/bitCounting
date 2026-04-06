# =============================================================================
# agents/tests_core_v2.py
# 40 tests para los 4 agentes core V2: INTAKE, CLASIFICADOR, FISCAL, ESTADOS.
# Fixtures con datos reales de Puerto Rico.
# =============================================================================

from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal
from typing import Any

from agents.core_decisions import AgentDecisionsLog, reset_decisions_log
from agents.intake_v2 import (
    IntakeAgentV2, IntakeResultV2, MissingCriticalFieldsError,
    LineItemV2, DocumentType,
)
from agents.clasificador_v2 import (
    ClasificadorAgentV2, ClassificationResultV2, ClassifiedLineItem,
    DEFAULT_ACCOUNT_CATALOG,
)
from agents.fiscal_v2 import FiscalAgentV2, FiscalResultV2, TaxType
from agents.estados_v2 import (
    EstadosAgentV2, FinancialStatementsV2, AlgebraicImbalanceError,
)
from agents.exceptions import BitCountingAgentError


# ---------------------------------------------------------------------------
# Mock Vision Client
# ---------------------------------------------------------------------------

class MockVisionClient:
    """Vision client falso para tests — retorna datos predefinidos."""
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def extract(self, image_bytes: bytes, source_format: str) -> dict[str, Any]:
        return self.response


# Respuesta completa tipica de una factura PR
FULL_PR_INVOICE = {
    "vendor":         "Inmobiliaria Caribe LLC",
    "date":           "2025-03-15",
    "amount":         "2500.00",
    "tax_amount":     "262.50",
    "currency":       "USD",
    "payment_method": "CHECK",
    "document_type":  "INVOICE",
    "line_items": [
        {
            "description": "Alquiler oficina piso 3 - Hato Rey",
            "quantity":    "1",
            "unit_price":  "2500.00",
            "amount":      "2500.00",
            "tax_amount":  "262.50",
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers reutilizables
# ---------------------------------------------------------------------------

def make_intake_result(
    vendor:     str             = "Suplidores del Norte PR",
    amount:     str             = "1000.00",
    date_str:   str             = "2025-06-15",
    tax_amount: str             = "105.00",
    line_items: tuple           = (),
    result_id:  str | None      = None,
) -> IntakeResultV2:
    kwargs: dict = dict(
        source_format="pdf",
        vendor=vendor,
        date=date_str,
        amount=Decimal(amount),
        tax_amount=Decimal(tax_amount),
        payment_method="CHECK",
        line_items=line_items,
        confidence=Decimal("0.85"),
    )
    if result_id:
        kwargs["result_id"] = result_id
    return IntakeResultV2(**kwargs)


def make_classification(items_data: list[dict]) -> ClassificationResultV2:
    items = tuple(
        ClassifiedLineItem(
            original_description=d["desc"],
            amount=Decimal(d["amount"]),
            account_code=d["code"],
            account_name=DEFAULT_ACCOUNT_CATALOG.get(d["code"], {}).get("name", "Test"),
            account_category=DEFAULT_ACCOUNT_CATALOG.get(d["code"], {}).get("category", "EXPENSE"),
            confidence=Decimal(d.get("conf", "0.85")),
            classification_rule=f"test → {d['code']}",
            is_unclassified=d.get("unclassified", False),
        )
        for d in items_data
    )
    total = sum(Decimal(d["amount"]) for d in items_data)
    unc   = sum(1 for d in items_data if d.get("unclassified"))
    return ClassificationResultV2(
        intake_result_id="test-intake-001",
        classified_items=items,
        total_amount=total,
        unclassified_count=unc,
        needs_cpa_review=unc > 0,
        average_confidence=Decimal("0.85") if not unc else Decimal("0.0"),
    )


@pytest.fixture(autouse=True)
def fresh_log():
    """Reinicia el log global antes de cada test."""
    reset_decisions_log()
    yield


# ===========================================================================
# INTAKE V2  (1-10)
# ===========================================================================

def test_intake_extracts_all_fields():
    log    = AgentDecisionsLog()
    agent  = IntakeAgentV2(vision_client=MockVisionClient(FULL_PR_INVOICE), decisions_log=log)
    result = agent.process_document(b"fake-image", "jpg")
    assert result.vendor        == "Inmobiliaria Caribe LLC"
    assert result.date          == "2025-03-15"
    assert result.amount        == Decimal("2500.00")
    assert result.tax_amount    == Decimal("262.50")
    assert result.currency      == "USD"
    assert result.payment_method== "CHECK"
    assert result.document_type == DocumentType.INVOICE
    assert len(result.line_items) == 1


def test_intake_missing_tax_amount_marked():
    data  = dict(FULL_PR_INVOICE)
    data.pop("tax_amount", None)
    data["tax_amount"] = None
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    result = agent.process_document(b"img", "jpg", raise_on_missing_critical=False)
    assert "tax_amount" in result.missing_fields


def test_intake_missing_amount_raises():
    data  = dict(FULL_PR_INVOICE)
    data["amount"] = None
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    with pytest.raises(MissingCriticalFieldsError) as exc_info:
        agent.process_document(b"img", "pdf")
    assert "amount" in exc_info.value.missing_critical


def test_intake_missing_date_raises():
    data  = dict(FULL_PR_INVOICE)
    data["date"] = None
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    with pytest.raises(MissingCriticalFieldsError) as exc_info:
        agent.process_document(b"img", "pdf")
    assert "date" in exc_info.value.missing_critical


def test_intake_missing_critical_no_raise():
    data  = dict(FULL_PR_INVOICE)
    data["amount"] = None
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    result = agent.process_document(b"img", "pdf", raise_on_missing_critical=False)
    assert isinstance(result, IntakeResultV2)
    assert result.critical_fields_missing is True


def test_intake_result_is_frozen():
    log    = AgentDecisionsLog()
    agent  = IntakeAgentV2(vision_client=MockVisionClient(FULL_PR_INVOICE), decisions_log=log)
    result = agent.process_document(b"img", "jpg")
    with pytest.raises(Exception):
        result.vendor = "Hacked"  # type: ignore[misc]


def test_intake_confidence_penalty_for_missing():
    data = {"vendor": "Test Inc"}  # sin amount, date, tax_amount, payment_method, line_items
    log  = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    result = agent.process_document(b"img", "jpg", raise_on_missing_critical=False)
    assert result.confidence < Decimal("0.50")


def test_intake_vision_error_returns_gracefully():
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient({"error": "connection timeout"}), decisions_log=log)
    result = agent.process_document(b"img", "jpg", raise_on_missing_critical=False)
    assert result.critical_fields_missing is True
    assert result.confidence == Decimal("0.0")


def test_intake_line_items_parsed():
    data = dict(FULL_PR_INVOICE)
    data["line_items"] = [
        {"description": "Servicio 1", "amount": "500.00"},
        {"description": "Servicio 2", "amount": "300.00"},
        {"description": "Servicio 3", "amount": "200.00"},
    ]
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(data), decisions_log=log)
    result = agent.process_document(b"img", "pdf")
    assert len(result.line_items) == 3


def test_intake_records_decision():
    log   = AgentDecisionsLog()
    agent = IntakeAgentV2(vision_client=MockVisionClient(FULL_PR_INVOICE), decisions_log=log)
    agent.process_document(b"img", "jpg")
    decisions = log.get_decisions("INTAKE_V2")
    assert len(decisions) == 1
    assert decisions[0].agent_name == "INTAKE_V2"


# ===========================================================================
# CLASIFICADOR V2  (11-20)
# ===========================================================================

def test_clasificador_alquiler_to_5200():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    intake = make_intake_result(vendor="Alquiler Oficina Hato Rey")
    items  = (LineItemV2(description="pago de alquiler oficina santurce mensual", amount=Decimal("2500.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    codes  = [i.account_code for i in result.classified_items]
    assert "5200" in codes


def test_clasificador_nomina_to_5100():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (LineItemV2(description="nomina empleados enero 2025", amount=Decimal("8000.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    codes  = [i.account_code for i in result.classified_items]
    assert "5100" in codes


def test_clasificador_ivu_to_5600():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (LineItemV2(description="ivu pagado compras enero", amount=Decimal("105.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    codes  = [i.account_code for i in result.classified_items]
    assert "5600" in codes


def test_clasificador_unknown_is_unclassified():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (LineItemV2(description="xyz abc 123 completamente desconocido qwerty", amount=Decimal("500.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    assert any(i.is_unclassified for i in result.classified_items)


def test_clasificador_unclassified_triggers_escalation():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (LineItemV2(description="zzz concepto no reconocido xyz", amount=Decimal("500.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    assert result.escalate_to_centinela is True
    assert result.unclassified_count >= 1


def test_clasificador_total_verified_via_sandbox():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (LineItemV2(description="alquiler oficina", amount=Decimal("2000.00")),)
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    assert result.sandbox_result_id is not None


def test_clasificador_result_is_frozen():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    intake = make_intake_result()
    result = agent.classify(intake)
    with pytest.raises(Exception):
        result.unclassified_count = 999  # type: ignore[misc]


def test_clasificador_low_confidence_needs_review():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    # Items con descripcion no reconocida → confidence 0.0 → needs_cpa_review
    items  = (
        LineItemV2(description="concepto zzzz desconocido", amount=Decimal("100.00")),
        LineItemV2(description="otro concepto aaa xyz",     amount=Decimal("200.00")),
    )
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    assert result.needs_cpa_review is True


def test_clasificador_multiple_items_classified():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    items  = (
        LineItemV2(description="alquiler oficina santurce", amount=Decimal("2500.00")),
        LineItemV2(description="factura electricidad aee",  amount=Decimal("350.00")),
        LineItemV2(description="nomina empleados",          amount=Decimal("5000.00")),
    )
    intake = make_intake_result(line_items=items)
    result = agent.classify(intake)
    assert len(result.classified_items) == 3


def test_clasificador_records_decision():
    log    = AgentDecisionsLog()
    agent  = ClasificadorAgentV2(decisions_log=log)
    intake = make_intake_result()
    agent.classify(intake)
    decisions = log.get_decisions("CLASIFICADOR_V2")
    assert len(decisions) == 1


# ===========================================================================
# FISCAL V2  (21-30)
# ===========================================================================

TXN_DATE = date(2025, 6, 15)


def test_fiscal_ivu_revenue_positive():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Ventas servicios", "amount": "1000.00", "code": "4000"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    ivu_lines = [l for l in result.tax_liabilities if l.tax_type == TaxType.IVU_ESTATAL]
    assert len(ivu_lines) >= 1
    assert ivu_lines[0].tax_amount > Decimal("0")


def test_fiscal_ivu_expense_negative():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Alquiler oficina", "amount": "2500.00", "code": "5200"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    ivu_lines = [l for l in result.tax_liabilities if l.tax_type == TaxType.IVU_ESTATAL]
    assert len(ivu_lines) >= 1
    assert ivu_lines[0].tax_amount < Decimal("0")


def test_fiscal_ivu_5600_is_direct_credit():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    amount = Decimal("105.00")
    cls    = make_classification([{"desc": "IVU pagado", "amount": str(amount), "code": "5600"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    ivu_lines = [l for l in result.tax_liabilities if l.tax_type == TaxType.IVU_ESTATAL]
    assert len(ivu_lines) >= 1
    assert ivu_lines[0].tax_amount == -amount


def test_fiscal_fica_payroll():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Nomina empleados", "amount": "3000.00", "code": "5100"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    fica_lines = [l for l in result.tax_liabilities if l.tax_type == TaxType.FICA_SS]
    assert len(fica_lines) >= 1
    assert fica_lines[0].tax_amount > Decimal("0")


def test_fiscal_no_tax_for_asset_accounts():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Transferencia efectivo", "amount": "5000.00", "code": "1000"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    no_tax = [l for l in result.tax_liabilities if l.tax_type == TaxType.NO_TAX]
    assert len(no_tax) >= 1
    assert no_tax[0].tax_amount == Decimal("0")


def test_fiscal_unclassified_skipped():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([
        {"desc": "Alquiler",     "amount": "2000.00", "code": "5200"},
        {"desc": "Desconocido",  "amount": "500.00",  "code": "9999", "unclassified": True},
    ])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    # Debe haber exactamente 1 linea (el unclassified se salta)
    assert len(result.tax_liabilities) == 1


def test_fiscal_all_calcs_via_sandbox():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([
        {"desc": "Ventas",  "amount": "1000.00", "code": "4000"},
        {"desc": "Alquiler","amount": "500.00",  "code": "5200"},
    ])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    for line in result.tax_liabilities:
        assert line.sandbox_result_id, f"sandbox_result_id vacio en {line.tax_type}"


def test_fiscal_result_is_frozen():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Ventas", "amount": "1000.00", "code": "4000"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    with pytest.raises(Exception):
        result.total_tax_due = Decimal("0")  # type: ignore[misc]


def test_fiscal_has_sandbox_result_ids_in_result():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Ventas", "amount": "1000.00", "code": "4000"}])
    result = agent.compute_fiscal_obligations(cls, TXN_DATE)
    assert isinstance(result.sandbox_result_ids, tuple)
    assert len(result.sandbox_result_ids) > 0


def test_fiscal_records_decision():
    log    = AgentDecisionsLog()
    agent  = FiscalAgentV2(decisions_log=log)
    cls    = make_classification([{"desc": "Ventas", "amount": "1000.00", "code": "4000"}])
    agent.compute_fiscal_obligations(cls, TXN_DATE)
    decisions = log.get_decisions("FISCAL_PR_V2")
    assert len(decisions) == 1


# ===========================================================================
# ESTADOS V2  (31-40)
# ===========================================================================

# Balance algebraicamente correcto: Activos=10000, Pasivos=4000, Capital=6000
BS_ACCOUNTS = {
    "1000": Decimal("10000.00"),   # Efectivo — ASSET
    "2000": Decimal("4000.00"),    # Cuentas por Pagar — LIABILITY
    "3000": Decimal("6000.00"),    # Capital — EQUITY
}

# Balance desbalanceado
IMBALANCED_ACCOUNTS = {
    "1000": Decimal("10000.00"),
    "2000": Decimal("3000.00"),    # Deberia ser 4000 para cuadrar
    "3000": Decimal("6000.00"),
}

# Para Estado de Resultados: Ingresos=20000, Gastos=5000, Utilidad=15000
IS_ACCOUNTS = {
    "4000": Decimal("20000.00"),   # Ingresos Ventas
    "5200": Decimal("3000.00"),    # Alquiler
    "5500": Decimal("2000.00"),    # Admin
}

P_FROM = date(2025, 1, 1)
P_TO   = date(2025, 12, 31)


def test_balance_sheet_algebraically_balanced():
    log    = AgentDecisionsLog()
    agent  = EstadosAgentV2(decisions_log=log)
    bs     = agent.generate_balance_sheet(BS_ACCOUNTS, P_TO, "Tech Solutions PR")
    assert bs.total_assets == bs.total_liabilities + bs.total_equity
    assert bs.algebraically_verified is True


def test_balance_sheet_imbalance_raises():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    with pytest.raises(AlgebraicImbalanceError) as exc_info:
        agent.generate_balance_sheet(IMBALANCED_ACCOUNTS, P_TO, "Test Corp")
    assert "Balance General" in str(exc_info.value)


def test_balance_sheet_is_frozen():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    bs    = agent.generate_balance_sheet(BS_ACCOUNTS, P_TO, "Test Corp")
    with pytest.raises(Exception):
        bs.total_assets = Decimal("99")  # type: ignore[misc]


def test_balance_sheet_xbrl_facts_populated():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    bs    = agent.generate_balance_sheet(BS_ACCOUNTS, P_TO, "Tech PR")
    assert len(bs.xbrl_facts) > 0


def test_balance_sheet_xbrl_tag_format():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    bs    = agent.generate_balance_sheet(BS_ACCOUNTS, P_TO, "Tech PR")
    for fact in bs.xbrl_facts:
        assert ":" in fact.tag, f"Tag sin namespace: {fact.tag}"


def test_income_statement_net_income_correct():
    log    = AgentDecisionsLog()
    agent  = EstadosAgentV2(decisions_log=log)
    is_    = agent.generate_income_statement(IS_ACCOUNTS, P_FROM, P_TO, "Tech PR")
    assert is_.total_revenue  == Decimal("20000.00")
    assert is_.total_expenses == Decimal("5000.00")
    assert is_.net_income     == Decimal("15000.00")


def test_income_statement_is_frozen():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    is_   = agent.generate_income_statement(IS_ACCOUNTS, P_FROM, P_TO, "Test")
    with pytest.raises(Exception):
        is_.net_income = Decimal("0")  # type: ignore[misc]


def test_cash_flow_closing_balance():
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    # Revenue=20000, Expenses=5000 → operating=15000. opening=5000 → closing=20000
    cf    = agent.generate_cash_flow(IS_ACCOUNTS, P_FROM, P_TO, "Tech PR", Decimal("5000.00"))
    assert cf.closing_balance == cf.opening_balance + cf.net_change
    assert cf.closing_balance == Decimal("20000.00")


def test_full_financial_statements_package():
    # Balance: Assets=15000, Liab=5000, Equity=10000
    full_accounts = {
        "1000": Decimal("15000.00"),   # Efectivo — ASSET
        "2000": Decimal("5000.00"),    # AP — LIABILITY
        "3000": Decimal("10000.00"),   # Capital — EQUITY
        "4000": Decimal("20000.00"),   # Revenue
        "5200": Decimal("3000.00"),    # Alquiler
        "5500": Decimal("2000.00"),    # Admin
    }
    log    = AgentDecisionsLog()
    agent  = EstadosAgentV2(decisions_log=log)
    pkg    = agent.generate_financial_statements(
        accounts=full_accounts,
        client_name="Consultores del Este PR LLC",
        period_from=P_FROM,
        period_to=P_TO,
        opening_cash=Decimal("0"),
    )
    assert isinstance(pkg, FinancialStatementsV2)
    assert pkg.all_verified is True
    assert pkg.balance_sheet.total_assets == Decimal("15000.00")
    assert pkg.income_statement.net_income == Decimal("15000.00")


def test_estados_records_decision():
    full_accounts = {
        "1000": Decimal("15000.00"),
        "2000": Decimal("5000.00"),
        "3000": Decimal("10000.00"),
        "4000": Decimal("20000.00"),
        "5200": Decimal("3000.00"),
        "5500": Decimal("2000.00"),
    }
    log   = AgentDecisionsLog()
    agent = EstadosAgentV2(decisions_log=log)
    agent.generate_financial_statements(
        accounts=full_accounts,
        client_name="Consultores PR",
        period_from=P_FROM,
        period_to=P_TO,
    )
    decisions = log.get_decisions("ESTADOS_V2")
    assert len(decisions) == 1
