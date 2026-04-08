# =============================================================================
# tests/test_integration_load.py
# Tests de carga y concurrencia para Bit-Counting.
#
# Verifica:
#   - 100 transacciones concurrentes sin race conditions
#   - Pausa CENTINELA de un cliente no bloquea a otros clientes
#   - El sistema continúa operando si un agente falla en una transacción
#   - Aislamiento de estado entre FlowCoordinator concurrentes
#   - Throughput básico (sin regresiones de rendimiento graves)
#
# NOTAS:
#   - Usa concurrent.futures.ThreadPoolExecutor (no asyncio) ya que
#     FlowCoordinator y sus agentes son síncronos.
#   - Los tests de carga son "smoke tests" — verifican que no hay crashes
#     ni corrupción de estado, no benchmarks de rendimiento.
# =============================================================================

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

import pytest

from agents.flow import FlowCoordinator, process_document
from agents.centinela_guardian import CentinelaGuardian, ClientConfig


# =============================================================================
# Documentos de prueba para carga
# =============================================================================

def _make_expense_doc(vendor: str = "Ferretería San Juan LLC", amount: str = "500.00") -> dict:
    return {
        "vendor":               vendor,
        "date":                 "2026-03-15",
        "amount":               Decimal(amount),
        "tax_amount":           Decimal("57.50"),   # 11.5% de $500
        "currency":             "USD",
        "payment_method":       "CHECK",
        "confidence":           Decimal("0.88"),
        "missing_fields":       [],
        "critical_fields_missing": False,
    }


def _make_low_confidence_doc() -> dict:
    return {
        "vendor":               None,
        "date":                 "2026-03-18",
        "amount":               Decimal("500.00"),
        "tax_amount":           None,
        "currency":             "USD",
        "payment_method":       None,
        "confidence":           Decimal("0.35"),
        "missing_fields":       ["vendor", "tax_amount", "payment_method"],
        "critical_fields_missing": False,
    }


# =============================================================================
# TestConcurrentTransactions
# =============================================================================

class TestConcurrentTransactions:
    """100 transacciones concurrentes sin race conditions ni corrupción de estado."""

    def test_100_concurrent_transactions_all_complete(self):
        """100 transacciones paralelas — todas deben terminar con final_decision definido."""
        docs = [_make_expense_doc(f"Vendedor-{i}", f"{100 + i}.00") for i in range(100)]

        results = []
        errors = []

        def process_one(doc: dict) -> str:
            try:
                result = process_document(doc, source_format="manual")
                return result.final_decision
            except Exception as e:
                return f"ERROR: {e}"

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(process_one, doc) for doc in docs]
            for future in as_completed(futures):
                outcome = future.result()
                if outcome.startswith("ERROR:"):
                    errors.append(outcome)
                else:
                    results.append(outcome)

        assert len(errors) == 0, f"Errores en transacciones concurrentes: {errors[:5]}"
        assert len(results) == 100

    def test_100_concurrent_transactions_valid_decisions(self):
        """Todas las transacciones retornan COMPLETED, FAILED o PAUSED — nunca None."""
        docs = [_make_expense_doc(f"Proveedor-{i}", f"{200 + i}.00") for i in range(100)]
        valid_decisions = {"COMPLETED", "FAILED", "PAUSED"}

        def process_one(doc: dict) -> str:
            result = process_document(doc, source_format="manual")
            return result.final_decision

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_one, doc) for doc in docs]
            decisions = [f.result() for f in as_completed(futures)]

        assert all(d in valid_decisions for d in decisions), \
            f"Decisiones inválidas encontradas: {set(decisions) - valid_decisions}"

    def test_concurrent_flow_coordinators_have_independent_state(self):
        """Cada FlowCoordinator tiene su propio estado — no comparten logs."""
        docs = [_make_expense_doc(f"Tienda-{i}", f"{300 + i}.00") for i in range(20)]
        coordinators = [FlowCoordinator() for _ in range(20)]

        def run_coordinator(idx: int) -> int:
            result = coordinators[idx].process(docs[idx], source_format="manual")
            return len(result.log)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_coordinator, i) for i in range(20)]
            log_lengths = [f.result() for f in as_completed(futures)]

        # Cada coordinador debe tener su propio log — no vacío
        assert all(length > 0 for length in log_lengths)

    def test_no_shared_state_between_process_document_calls(self):
        """process_document crea FlowCoordinator fresco — no hay estado compartido."""
        results = []

        def run() -> str:
            doc = _make_expense_doc("Ferretería PR", "750.00")
            result = process_document(doc)
            return result.final_decision

        threads = [threading.Thread(target=lambda: results.append(run())) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        assert all(r in ("COMPLETED", "FAILED", "PAUSED") for r in results)

    def test_transaction_ids_are_unique_under_concurrency(self):
        """Los UUIDs generados concurrentemente son únicos."""
        ids_seen: list[str] = []
        lock = threading.Lock()

        def generate_id() -> None:
            new_id = str(uuid.uuid4())
            with lock:
                ids_seen.append(new_id)

        threads = [threading.Thread(target=generate_id) for _ in range(1000)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids_seen) == len(set(ids_seen)), "IDs UUID duplicados detectados"


# =============================================================================
# TestCentinelaFaultIsolation
# =============================================================================

class TestCentinelaFaultIsolation:
    """Pausa de CENTINELA en un cliente no bloquea a otros clientes."""

    def test_paused_client_does_not_block_other_clients(self):
        """
        CLIENT A tiene una transacción con baja confianza (→ PAUSED).
        CLIENT B simultáneamente procesa una transacción limpia (→ COMPLETED).
        Las dos deben terminar de forma independiente.
        """
        client_a_doc = _make_low_confidence_doc()
        client_b_doc = _make_expense_doc("Costco PR", "1000.00")

        results: dict[str, str] = {}
        errors: list[str] = []

        def process_client_a() -> None:
            try:
                result = process_document(client_a_doc, source_format="manual")
                results["A"] = result.final_decision
            except Exception as e:
                errors.append(f"A: {e}")

        def process_client_b() -> None:
            try:
                result = process_document(client_b_doc, source_format="manual")
                results["B"] = result.final_decision
            except Exception as e:
                errors.append(f"B: {e}")

        t_a = threading.Thread(target=process_client_a)
        t_b = threading.Thread(target=process_client_b)
        t_a.start()
        t_b.start()
        t_a.join(timeout=30)
        t_b.join(timeout=30)

        assert len(errors) == 0, f"Errores: {errors}"
        assert "A" in results and "B" in results
        # B debe completarse independientemente del estado de A
        assert results["B"] in ("COMPLETED", "FAILED", "PAUSED")

    def test_centinela_guardian_client_isolation(self):
        """
        CentinelaGuardian no permite que una pausa de un cliente
        afecte las evaluaciones de otro cliente.
        """
        cfg_a = ClientConfig(
            client_id="client-a",
            confidence_threshold=Decimal("0.75"),
            sla_hours_default=48,
            assigned_cpa_license="CPA-001",
        )
        cfg_b = ClientConfig(
            client_id="client-b",
            confidence_threshold=Decimal("0.75"),
            sla_hours_default=48,
            assigned_cpa_license="CPA-002",
        )

        centinela_a = CentinelaGuardian()
        centinela_b = CentinelaGuardian()

        # Pausar una transacción de A
        decision_a = centinela_a.evaluate(
            transaction_id="txn-a-001",
            client_id="client-a",
            transaction_date="2026-03-15",
            amount=Decimal("500.00"),
            vendor=None,
            transaction_type="EXPENSE",
            confidence=Decimal("0.40"),    # baja → PAUSE
            current_rule_id="IVU_PR_2015",
            history_90d=(),
            history_6m=(),
            cross_check_results={},
            audit_result=None,
            client_config=cfg_a,
        )

        # B en instancia separada → nunca afectada
        decision_b = centinela_b.evaluate(
            transaction_id="txn-b-001",
            client_id="client-b",
            transaction_date="2026-03-15",
            amount=Decimal("1200.00"),
            vendor="Ferretería San Juan",
            transaction_type="EXPENSE",
            confidence=Decimal("0.88"),    # alta → PROCEED
            current_rule_id="IVU_PR_2015",
            history_90d=(),
            history_6m=(),
            cross_check_results={},
            audit_result=None,
            client_config=cfg_b,
        )

        assert decision_a.decision == "PAUSE"
        assert decision_b.decision == "PROCEED"

    def test_multiple_concurrent_centinela_evaluations(self):
        """50 evaluaciones CENTINELA concurrentes sin corrupción de estado."""
        decisions: list[str] = []
        lock = threading.Lock()

        cfg = ClientConfig(
            client_id="client-load",
            confidence_threshold=Decimal("0.75"),
            sla_hours_default=48,
            assigned_cpa_license="CPA-LOAD",
        )

        def evaluate_one(i: int) -> None:
            centinela = CentinelaGuardian()   # instancia fresca por evaluación
            decision = centinela.evaluate(
                transaction_id=f"txn-load-{i}",
                client_id="client-load",
                transaction_date="2026-03-15",
                amount=Decimal(str(100 + i)),
                vendor="Vendedor PR",
                transaction_type="EXPENSE",
                confidence=Decimal("0.88"),
                current_rule_id="IVU_PR_2015",
                history_90d=(),
                history_6m=(),
                cross_check_results={},
                audit_result=None,
                client_config=cfg,
            )
            with lock:
                decisions.append(decision.decision)

        threads = [threading.Thread(target=evaluate_one, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(decisions) == 50
        assert all(d in ("PROCEED", "PAUSE") for d in decisions)


# =============================================================================
# TestAgentFailureTolerance
# =============================================================================

class TestAgentFailureTolerance:
    """El sistema continúa si una transacción individual falla."""

    def test_one_failure_does_not_block_batch(self):
        """
        En un lote de 20 transacciones, si 2 de ellas tienen datos que
        provocan FAILED (IVU inválido), las demás 18 continúan normalmente.
        """
        docs = [_make_expense_doc(f"Proveedor-{i}", f"{500 + i}.00") for i in range(18)]

        # 2 documentos con IVU imposible → FAILED
        bad_doc = {
            "vendor":               "Proveedora Incorrecta SA",
            "date":                 "2026-03-10",
            "amount":               Decimal("1000.00"),
            "tax_amount":           Decimal("999.00"),   # imposible
            "currency":             "USD",
            "payment_method":       "CASH",
            "confidence":           Decimal("0.91"),
            "missing_fields":       [],
            "critical_fields_missing": False,
        }
        docs += [bad_doc, bad_doc]

        results: dict[str, int] = {"COMPLETED": 0, "PAUSED": 0, "FAILED": 0}
        lock = threading.Lock()

        def run(doc: dict) -> None:
            try:
                result = process_document(doc, source_format="manual")
                with lock:
                    results[result.final_decision] = results.get(result.final_decision, 0) + 1
            except Exception:
                with lock:
                    results["ERROR"] = results.get("ERROR", 0) + 1

        threads = [threading.Thread(target=run, args=(d,)) for d in docs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = sum(results.values())
        assert total == 20, f"Alguna transacción no se procesó. Resultados: {results}"
        assert results.get("ERROR", 0) == 0, "No deben haber errores no controlados"
        assert results.get("FAILED", 0) >= 2, "Las 2 docs con IVU inválido deben marcar FAILED"

    def test_bad_date_does_not_crash_pipeline(self):
        """Una fecha no laborable genera discrepancia, no crash."""
        doc = _make_expense_doc()
        doc["date"] = "2025-04-18"  # Viernes Santo PR 2025 — día no laborable

        result = process_document(doc, source_format="manual")
        # Puede ser FAILED (si auditor rechaza) o COMPLETED — pero nunca exception
        assert result.final_decision in ("COMPLETED", "FAILED", "PAUSED")

    def test_missing_optional_fields_do_not_crash(self):
        """Campos opcionales ausentes no provocan crash en el pipeline."""
        minimal_doc = {
            "vendor":               "Proveedor Mínimo",
            "date":                 "2026-03-15",
            "amount":               Decimal("100.00"),
            "tax_amount":           None,
            "currency":             "USD",
            "payment_method":       None,
            "confidence":           Decimal("0.80"),
            "missing_fields":       ["tax_amount", "payment_method"],
            "critical_fields_missing": False,
        }
        result = process_document(minimal_doc, source_format="manual")
        assert result.final_decision in ("COMPLETED", "FAILED", "PAUSED")


# =============================================================================
# TestThroughputBaseline
# =============================================================================

class TestThroughputBaseline:
    """Smoke tests de throughput — verifica que no hay regresiones graves."""

    def test_single_transaction_completes_under_5_seconds(self):
        """Una transacción limpia debe procesarse en menos de 5 segundos."""
        doc = _make_expense_doc()
        start = time.perf_counter()
        result = process_document(doc, source_format="manual")
        elapsed = time.perf_counter() - start

        assert result.final_decision in ("COMPLETED", "FAILED", "PAUSED")
        assert elapsed < 5.0, f"Pipeline tardó demasiado: {elapsed:.2f}s"

    def test_10_sequential_transactions_under_30_seconds(self):
        """10 transacciones secuenciales en menos de 30 segundos."""
        docs = [_make_expense_doc(f"Vendedor-{i}", f"{100 + i * 50}.00") for i in range(10)]
        start = time.perf_counter()
        for doc in docs:
            result = process_document(doc, source_format="manual")
            assert result.final_decision in ("COMPLETED", "FAILED", "PAUSED")
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"10 transacciones tardaron demasiado: {elapsed:.2f}s"

    def test_50_concurrent_transactions_under_60_seconds(self):
        """50 transacciones concurrentes en menos de 60 segundos."""
        docs = [_make_expense_doc(f"Concurrente-{i}", f"{200 + i}.00") for i in range(50)]
        start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_document, doc, "manual") for doc in docs]
            results = [f.result() for f in as_completed(futures)]

        elapsed = time.perf_counter() - start
        assert len(results) == 50
        assert elapsed < 60.0, f"50 transacciones concurrentes tardaron demasiado: {elapsed:.2f}s"
