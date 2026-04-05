# =============================================================================
# agents/auditor.py
# Agente AUDITOR — verificacion cruzada independiente del CLASIFICADOR.
#
# GARANTIAS DE DISENO:
#   - COMPLETAMENTE AISLADO: no accede a resultados del CLASIFICADOR antes
#     de hacer su propia verificacion independiente.
#   - Verifica balance algebraico exacto: debito == credito (tolerancia $0.00).
#   - Detecta senales de fraude, anomalias y errores matematicos.
#   - Opera SOLO sobre ClassifierOutput e IntakeOutput — jamas sobre strings.
# =============================================================================

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from .base import BaseAgent
from .exceptions import AgentScopeError
from .messages import AuditorVerification, ClassifierOutput, EntryType, IntakeOutput
from .clasificador import get_chart_of_accounts

# ---------------------------------------------------------------------------
# Puerto Rico Non-Working Days 2025 (hardcoded)
# ---------------------------------------------------------------------------

_PR_NON_WORKING_DAYS_2025: frozenset[date] = frozenset([
    date(2025, 1, 1),   # New Year's Day
    date(2025, 1, 6),   # Three Kings Day / Dia de Reyes
    date(2025, 1, 20),  # MLK Day (3rd Monday January)
    date(2025, 2, 17),  # Presidents Day (3rd Monday February)
    date(2025, 3, 22),  # Emancipation Day PR (Abolicion de la Esclavitud)
    date(2025, 4, 18),  # Good Friday (Viernes Santo)
    date(2025, 5, 26),  # Memorial Day (last Monday May)
    date(2025, 7, 4),   # Independence Day
    date(2025, 7, 25),  # Constitution Day PR (Dia de la Constitucion)
    date(2025, 9, 1),   # Labor Day (1st Monday September)
    date(2025, 11, 19), # Discovery Day PR (Dia del Descubrimiento)
    date(2025, 11, 27), # Thanksgiving (4th Thursday November)
    date(2025, 12, 25), # Christmas Day
])

# IVU rate in Puerto Rico
_IVU_RATE = Decimal("0.115")
_IVU_TOLERANCE = Decimal("0.01")

# Thresholds
_LARGE_TRANSACTION_THRESHOLD = Decimal("50000.00")
_VENDOR_REQUIRED_THRESHOLD = Decimal("500.00")
_ANOMALY_MULTIPLIER = Decimal("3")

# Fraud signals: new vendor with unusually large amount
_FRAUD_NEW_VENDOR_THRESHOLD = Decimal("5000.00")


class AuditorAgent(BaseAgent):
    """
    Agente AUDITOR: verificacion cruzada independiente del CLASIFICADOR.

    Opera de forma completamente aislada — no accede al razonamiento interno
    del CLASIFICADOR antes de hacer su propia verificacion independiente.

    Detecta:
      - Errores de balance algebraico (debito != credito)
      - IVU calculado incorrectamente (tolerancia ±$0.01)
      - Transacciones en dias no laborables de PR
      - Montos inusuales o fuera de rango
      - Senales de fraude (vendedor nuevo + monto inusual)
      - Codigos de cuenta que no existen en el catalogo
    """

    @property
    def agent_name(self) -> str:
        return "AUDITOR"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    @property
    def allowed_input_types(self) -> tuple[type, ...]:
        # AUDITOR recibe ClassifierOutput del CLASIFICADOR
        return (ClassifierOutput,)

    @property
    def allowed_output_types(self) -> tuple[type, ...]:
        return (AuditorVerification,)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        debit_entry: ClassifierOutput,
        credit_entry: ClassifierOutput,
        intake_output: IntakeOutput,
    ) -> AuditorVerification:
        """
        Verificacion cruzada independiente del par de asientos contables.

        Verifica:
          1. Balance algebraico exacto: debit.amount == credit.amount ($0.00 tolerancia)
          2. IVU: si tax_amount presente en intake, verifica 11.5% de base (±$0.01)
          3. Fecha: flag si es dia no laborable en PR
          4. Monto razonable: flag si > $50,000
          5. Vendedor: flag si vendor es None para montos > $500
          6. Fraude: vendedor nuevo + monto inusual
          7. Codigos de cuenta existen en el catalogo

        Args:
            debit_entry:   ClassifierOutput con entry_type=DEBIT del CLASIFICADOR.
            credit_entry:  ClassifierOutput con entry_type=CREDIT del CLASIFICADOR.
            intake_output: IntakeOutput original para contexto adicional.

        Returns:
            AuditorVerification con resultado completo de todos los checks.
        """
        checks_passed: list[str] = []
        discrepancies: list[str] = []
        fraud_flags: list[str] = []
        chart = get_chart_of_accounts()

        # ----------------------------------------------------------------
        # CHECK 1: Algebraic balance — debit.amount == credit.amount
        # ----------------------------------------------------------------
        algebraic_balance_ok = (debit_entry.amount == credit_entry.amount)
        if algebraic_balance_ok:
            checks_passed.append("algebraic_balance")
        else:
            discrepancies.append(
                f"BALANCE ROTO: debito ${debit_entry.amount} != "
                f"credito ${credit_entry.amount}. "
                "La partida doble debe ser algebraicamente balanceada."
            )

        # ----------------------------------------------------------------
        # CHECK 2: Entry types are correct (debit is DEBIT, credit is CREDIT)
        # ----------------------------------------------------------------
        if debit_entry.entry_type == EntryType.DEBIT:
            checks_passed.append("debit_entry_type_correct")
        else:
            discrepancies.append(
                f"TIPO INCORRECTO: se esperaba entry_type=DEBIT en debit_entry, "
                f"recibido: {debit_entry.entry_type}."
            )

        if credit_entry.entry_type == EntryType.CREDIT:
            checks_passed.append("credit_entry_type_correct")
        else:
            discrepancies.append(
                f"TIPO INCORRECTO: se esperaba entry_type=CREDIT en credit_entry, "
                f"recibido: {credit_entry.entry_type}."
            )

        # ----------------------------------------------------------------
        # CHECK 3: Account codes exist in the Chart of Accounts
        # ----------------------------------------------------------------
        debit_code_valid = debit_entry.account_code in chart
        credit_code_valid = credit_entry.account_code in chart

        if debit_code_valid:
            checks_passed.append("debit_account_code_valid")
        else:
            discrepancies.append(
                f"CODIGO INVALIDO: cuenta debito '{debit_entry.account_code}' "
                "no existe en el Plan de Cuentas de Bit-Counting."
            )

        if credit_code_valid:
            checks_passed.append("credit_account_code_valid")
        else:
            discrepancies.append(
                f"CODIGO INVALIDO: cuenta credito '{credit_entry.account_code}' "
                "no existe en el Plan de Cuentas de Bit-Counting."
            )

        # ----------------------------------------------------------------
        # CHECK 4: IVU verification (if tax_amount present in intake)
        # ----------------------------------------------------------------
        if intake_output.tax_amount is not None and intake_output.amount is not None:
            expected_ivu = (intake_output.amount * _IVU_RATE).quantize(Decimal("0.01"))
            actual_ivu = intake_output.tax_amount
            ivu_diff = abs(actual_ivu - expected_ivu)

            if ivu_diff <= _IVU_TOLERANCE:
                checks_passed.append("ivu_calculation_correct")
            else:
                discrepancies.append(
                    f"IVU INCORRECTO: IVU reportado ${actual_ivu}, "
                    f"esperado ${expected_ivu} (11.5% de base ${intake_output.amount}). "
                    f"Diferencia: ${ivu_diff} (tolerancia: ±${_IVU_TOLERANCE})."
                )
        else:
            checks_passed.append("ivu_not_applicable")

        # ----------------------------------------------------------------
        # CHECK 5: Transaction date — flag if PR non-working day
        # ----------------------------------------------------------------
        if intake_output.date is not None:
            try:
                txn_date = date.fromisoformat(intake_output.date)
                if txn_date in _PR_NON_WORKING_DAYS_2025:
                    discrepancies.append(
                        f"DIA NO LABORABLE: la transaccion tiene fecha {intake_output.date}, "
                        "que es un dia feriado o no laborable en Puerto Rico (2025). "
                        "Verificar si la fecha es correcta."
                    )
                else:
                    checks_passed.append("transaction_date_working_day")
            except ValueError:
                discrepancies.append(
                    f"FECHA INVALIDA: no se pudo parsear la fecha '{intake_output.date}' "
                    "como ISO 8601 (YYYY-MM-DD)."
                )
        else:
            checks_passed.append("date_not_available_skip")

        # ----------------------------------------------------------------
        # CHECK 6: Amount reasonableness — flag if > $50,000
        # ----------------------------------------------------------------
        amount = intake_output.amount or Decimal("0.00")
        if amount > _LARGE_TRANSACTION_THRESHOLD:
            discrepancies.append(
                f"MONTO INUSUAL: ${amount} supera el umbral de ${_LARGE_TRANSACTION_THRESHOLD} "
                "para transacciones estandar. Requiere autorizacion adicional y revision CPA."
            )
        else:
            checks_passed.append("amount_reasonableness_ok")

        # ----------------------------------------------------------------
        # CHECK 7: Vendor required for amounts > $500
        # ----------------------------------------------------------------
        if intake_output.vendor is None and amount > _VENDOR_REQUIRED_THRESHOLD:
            discrepancies.append(
                f"VENDEDOR FALTANTE: el vendedor no esta especificado para una transaccion "
                f"de ${amount}, que supera el umbral de ${_VENDOR_REQUIRED_THRESHOLD}. "
                "Se requiere identificar el vendedor para montos mayores a $500."
            )
        else:
            checks_passed.append("vendor_presence_ok")

        # ----------------------------------------------------------------
        # CHECK 8: Fraud signal — new vendor + unusual amount
        # ----------------------------------------------------------------
        is_new_vendor = self._is_new_vendor(intake_output.vendor)
        if is_new_vendor and amount > _FRAUD_NEW_VENDOR_THRESHOLD:
            fraud_flags.append(
                f"FRAUDE POTENCIAL: vendedor '{intake_output.vendor}' parece ser nuevo "
                f"(no reconocido en el catalogo) y el monto ${amount} supera "
                f"${_FRAUD_NEW_VENDOR_THRESHOLD}. Requiere verificacion manual inmediata."
            )
        else:
            checks_passed.append("fraud_new_vendor_check_ok")

        # ----------------------------------------------------------------
        # CHECK 9: Both entries reference the same intake transaction
        # ----------------------------------------------------------------
        if debit_entry.classified_intake_id == credit_entry.classified_intake_id:
            checks_passed.append("entries_same_transaction")
        else:
            discrepancies.append(
                "INCONSISTENCIA DE TRAZABILIDAD: los asientos debito y credito referencian "
                f"transacciones distintas: '{debit_entry.classified_intake_id}' vs "
                f"'{credit_entry.classified_intake_id}'."
            )

        # ----------------------------------------------------------------
        # INDEPENDENT VERIFICATION: AUDITOR re-derives account code
        # ----------------------------------------------------------------
        independent_code, independent_rule = self._independent_classify(intake_output)

        # ----------------------------------------------------------------
        # Final verdict
        # ----------------------------------------------------------------
        verified = len(discrepancies) == 0 and len(fraud_flags) == 0

        return AuditorVerification(
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            source_agent=self.agent_name,
            target_agent="ORQUESTADOR",
            verified=verified,
            checks_passed=tuple(checks_passed),
            discrepancies=tuple(discrepancies),
            fraud_flags=tuple(fraud_flags),
            algebraic_balance_ok=algebraic_balance_ok,
            independent_account_code=independent_code,
            independent_rule_ref=independent_rule,
            verified_classifier_id=debit_entry.message_id,
        )

    def detect_anomalies(
        self,
        intake_output: IntakeOutput,
        client_history: list[dict],
    ) -> list[str]:
        """
        Compara la transaccion contra el historial del cliente para detectar anomalias.

        Flags:
          - Monto > 3x el promedio historico para el mismo tipo de transaccion.
          - Transacciones en horas inusuales (si el timestamp esta disponible).

        Args:
            intake_output:  IntakeOutput de la transaccion a evaluar.
            client_history: Lista de dicts con historial de transacciones del cliente.
                            Formato esperado: [{"amount": Decimal, "type": str, ...}, ...]

        Returns:
            Lista de descripciones de anomalias. Vacia si no hay anomalias.
        """
        anomalies: list[str] = []
        amount = intake_output.amount or Decimal("0.00")

        # --- Amount vs historical average ---
        if client_history:
            similar_amounts = [
                Decimal(str(tx["amount"]))
                for tx in client_history
                if "amount" in tx and tx.get("amount") is not None
            ]
            if similar_amounts:
                avg = sum(similar_amounts) / Decimal(str(len(similar_amounts)))
                if avg > Decimal("0") and amount > (_ANOMALY_MULTIPLIER * avg):
                    anomalies.append(
                        f"ANOMALIA DE MONTO: ${amount} es {(amount / avg):.1f}x el promedio "
                        f"historico del cliente (${avg:.2f}). Supera el umbral de "
                        f"{_ANOMALY_MULTIPLIER}x para transacciones similares."
                    )

        # --- Unusual hour check ---
        if intake_output.timestamp is not None:
            hour = intake_output.timestamp.hour
            # Unusual hours: midnight to 5 AM UTC (accounting transactions at odd hours)
            if 0 <= hour < 5:
                anomalies.append(
                    f"HORA INUSUAL: transaccion registrada a las {hour:02d}:xx UTC "
                    "(entre medianoche y las 5 AM). Verificar si es una transaccion legitima."
                )

        return anomalies

    # ------------------------------------------------------------------
    # BaseAgent._process_impl — framework entry point
    # ------------------------------------------------------------------

    def _process_impl(self, message: ClassifierOutput) -> AuditorVerification:
        """
        Framework entry point. Performs independent verification of a single
        ClassifierOutput. For full double-entry verification, use verify() directly.
        """
        # When used via process() framework, we only have the debit entry.
        # We create a synthetic credit entry using the contra fields for balance check.
        chart = get_chart_of_accounts()

        if (
            message.contra_account_code is not None
            and message.contra_account_code in chart
            and message.contra_entry_type == EntryType.CREDIT
        ):
            contra_acct = chart[message.contra_account_code]
            synthetic_credit = ClassifierOutput(
                message_id=str(uuid.uuid4()),
                timestamp=message.timestamp,
                source_agent=message.source_agent,
                target_agent=message.target_agent,
                account_code=message.contra_account_code,
                account_name=contra_acct["name"],
                entry_type=EntryType.CREDIT,
                amount=message.amount,
                rule_ref=message.rule_ref,
                confidence=message.confidence,
                reasoning=message.reasoning,
                contra_account_code=message.account_code,
                contra_account_name=message.account_name,
                contra_entry_type=EntryType.DEBIT,
                classified_intake_id=message.classified_intake_id,
            )
        else:
            # Cannot reconstruct full double entry — report discrepancy
            return AuditorVerification(
                message_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc),
                source_agent=self.agent_name,
                target_agent="ORQUESTADOR",
                verified=False,
                checks_passed=(),
                discrepancies=(
                    "AUDITOR no pudo reconstruir el asiento de contrapartida desde "
                    "los campos contra_* del ClassifierOutput. Use verify() directamente "
                    "con ambos asientos para verificacion completa.",
                ),
                fraud_flags=(),
                algebraic_balance_ok=False,
                independent_account_code=message.account_code,
                independent_rule_ref=message.rule_ref,
                verified_classifier_id=message.message_id,
            )

        # Build a minimal IntakeOutput for context
        synthetic_intake = IntakeOutput(
            message_id=message.classified_intake_id,
            timestamp=message.timestamp,
            source_agent="INTAKE",
            target_agent="CENTINELA",
            amount=message.amount,
            confidence=message.confidence,
        )

        return self.verify(message, synthetic_credit, synthetic_intake)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _independent_classify(
        self,
        intake_output: IntakeOutput,
    ) -> tuple[str, str]:
        """
        AUDITOR independently derives an account code for cross-verification.
        Uses the same keyword rules as CLASIFICADOR but applied independently.
        Returns (account_code, rule_ref).
        """
        # Import here to make explicit that AUDITOR does NOT share state with
        # CLASIFICADOR — it imports only the static data (rules), not the agent.
        from .clasificador import _ALL_RULES  # noqa: PLC0415

        vendor_lower = (intake_output.vendor or "").lower()
        amount = intake_output.amount or Decimal("0.00")

        best_code = "5900"
        best_rule = "GAAP-PR-5900-MISC"
        best_score = 0

        for debit_code, _credit_code, _conf, rule_ref, keywords in _ALL_RULES:
            score = sum(1 for kw in keywords if kw in vendor_lower)
            if score > best_score:
                best_score = score
                best_code = debit_code
                best_rule = rule_ref

        return best_code, best_rule

    def _is_new_vendor(self, vendor: str | None) -> bool:
        """
        Heuristic: considers a vendor 'new' if it is not found in the known
        vendor keyword lists (same static catalog the CLASIFICADOR uses).
        In Phase 1, any vendor that doesn't match any known keyword is treated
        as potentially new/unknown.
        """
        if vendor is None:
            return True

        from .clasificador import _ALL_RULES  # noqa: PLC0415

        vendor_lower = vendor.lower()
        for _d, _c, _conf, _rule, keywords in _ALL_RULES:
            if any(kw in vendor_lower for kw in keywords):
                return False  # Recognized vendor
        return True  # No keyword matched — treat as new/unknown vendor
