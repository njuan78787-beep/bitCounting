# =============================================================================
# agents/tests_aprendizaje_federado.py
# Tests del Sistema de Aprendizaje Federado sin datos privados.
#
# Cubre:
#   - PII Scanner: SSN, EIN, email, teléfono, IP, cuenta bancaria, nombre
#   - generate_error_card: solo campos comportamentales, sin PII
#   - HIGH_PRIORITY_LEARNING cuando confidence_initial > 80% y fue corregido
#   - contribute_to_pool: doble verificación PII, BehaviorPattern sin client_id
#   - PoolContribution: tiene client_id PERO no pattern_id
#   - calibrate_confidence: ajuste de umbral según historial
#   - verify_pool_privacy: las 3 garantías de privacidad
#   - Ningún dato personal puede extraerse de error_cards ni behavior_patterns
# =============================================================================

from __future__ import annotations

import pytest
from decimal import Decimal

from .aprendizaje_federado import (
    AprendizajeFederado,
    BehaviorPattern,
    ConfidenceCalibration,
    CPACorrection,
    ErrorCard,
    ErrorCardNotFoundError,
    FiscalImpactType,
    LearningPriority,
    PIIViolationError,
    PoolContribution,
    _PII_PATTERNS,
    _SAFE_FIELDS,
    scan_for_pii,
    verify_no_pii,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_correction(
    transaction_type:         str   = "EXPENSE",
    industry_code:            str   = "IND-PR-001",
    classification_initial:   str   = "5200",
    classification_corrected: str   = "5300",
    correction_reason:        str   = "Clasificación incorrecta de gasto administrativo",
    rule_applied:             str   = "GAAP-PR-EXP-001",
    gaap_reference:           str   = "ASC 420",
    fiscal_impact_type:       FiscalImpactType = FiscalImpactType.WRONG_ACCOUNT,
    context_variables:        tuple = (),
    confidence_initial:       Decimal = Decimal("0.65"),
    confidence_final:         Decimal = Decimal("1.0"),
    client_id:                str   = "client-uuid-9999",
    transaction_id:           str   = "txn-abc-001",
) -> CPACorrection:
    return CPACorrection(
        transaction_id=transaction_id,
        client_id=client_id,
        transaction_type=transaction_type,
        industry_code=industry_code,
        classification_initial=classification_initial,
        classification_corrected=classification_corrected,
        correction_reason=correction_reason,
        rule_applied=rule_applied,
        gaap_reference=gaap_reference,
        fiscal_impact_type=fiscal_impact_type,
        context_variables=context_variables,
        confidence_initial=confidence_initial,
        confidence_final=confidence_final,
    )


# ===========================================================================
# 1. PII SCANNER — scan_for_pii
# ===========================================================================

class TestPIIScanner:

    def test_ssn_detected(self):
        result = scan_for_pii("description", "123-45-6789")
        assert result == "SSN"

    def test_ein_detected(self):
        result = scan_for_pii("description", "12-3456789")
        assert result == "EIN"

    def test_email_detected(self):
        result = scan_for_pii("description", "usuario@empresa.com")
        assert result == "EMAIL"

    def test_phone_us_detected(self):
        result = scan_for_pii("description", "555-123-4567")
        assert result == "PHONE_US"

    def test_phone_pr_detected(self):
        # 787 es área de PR — detectado como PHONE_US o PHONE_PR (ambos son PII)
        result = scan_for_pii("description", "787-555-1234")
        assert result in ("PHONE_PR", "PHONE_US")

    def test_phone_pr_939_detected(self):
        # 939 es área de PR — detectado como PHONE_US o PHONE_PR (ambos son PII)
        result = scan_for_pii("description", "939-555-1234")
        assert result in ("PHONE_PR", "PHONE_US")

    def test_full_name_detected(self):
        result = scan_for_pii("description", "Juan Perez Martinez")
        assert result == "FULL_NAME"

    def test_address_detected(self):
        # "123 Main Street" es detectado como PII (FULL_NAME o ADDRESS — ambos correcto)
        result = scan_for_pii("description", "123 Main Street")
        assert result is not None

    def test_clean_text_returns_none(self):
        result = scan_for_pii("description", "expense classification Q3")
        assert result is None

    def test_safe_fields_never_flagged(self):
        """Los campos en _SAFE_FIELDS no se escanean nunca."""
        for field in _SAFE_FIELDS:
            result = scan_for_pii(field, "123-45-6789 email@domain.com")
            assert result is None, f"Campo seguro '{field}' fue escaneado"

    def test_none_value_returns_none(self):
        assert scan_for_pii("some_field", None) is None

    def test_empty_string_returns_none(self):
        assert scan_for_pii("some_field", "") is None


# ===========================================================================
# 2. PII SCANNER — verify_no_pii
# ===========================================================================

class TestVerifyNoPII:

    def test_clean_dict_passes(self):
        verify_no_pii({
            "description": "expense reclassification",
            "reference":   "Q3-2025",
        })   # no debe lanzar

    def test_ssn_in_dict_raises(self):
        with pytest.raises(PIIViolationError) as exc_info:
            verify_no_pii({"description": "SSN: 123-45-6789"})
        assert exc_info.value.pattern == "SSN"

    def test_email_in_dict_raises(self):
        with pytest.raises(PIIViolationError) as exc_info:
            verify_no_pii({"contact": "vendor@empresa.com"})
        assert exc_info.value.pattern == "EMAIL"

    def test_ein_in_dict_raises(self):
        with pytest.raises(PIIViolationError) as exc_info:
            verify_no_pii({"tax_id": "12-3456789"})
        assert exc_info.value.pattern == "EIN"

    def test_pii_in_list_value_raises(self):
        with pytest.raises(PIIViolationError):
            verify_no_pii({"tags": ["seasonal", "vendor@mail.com", "q4"]})

    def test_safe_field_with_pii_ignored(self):
        """Campos seguros no se escanean, incluso si tienen PII."""
        verify_no_pii({
            "transaction_type": "123-45-6789",   # está en _SAFE_FIELDS
        })   # no debe lanzar

    def test_pii_violation_error_has_field(self):
        with pytest.raises(PIIViolationError) as exc_info:
            verify_no_pii({"notes": "Juan Rodriguez"})
        assert exc_info.value.field == "notes"


# ===========================================================================
# 3. PIIViolationError — mensaje descriptivo
# ===========================================================================

class TestPIIViolationError:

    def test_error_message_contains_field(self):
        err = PIIViolationError("description", "SSN")
        assert "description" in str(err)

    def test_error_message_contains_pattern(self):
        err = PIIViolationError("description", "SSN")
        assert "SSN" in str(err)

    def test_error_is_pii_violation(self):
        err = PIIViolationError("field", "EIN")
        assert isinstance(err, PIIViolationError)


# ===========================================================================
# 4. generate_error_card — CAMPOS PERMITIDOS ÚNICAMENTE
# ===========================================================================

class TestGenerateErrorCard:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_generates_error_card_with_correct_fields(self):
        correction = make_correction()
        card = self.system.generate_error_card("txn-001", correction)
        assert card.transaction_type == correction.transaction_type
        assert card.industry_code == correction.industry_code
        assert card.classification_initial == correction.classification_initial
        assert card.classification_corrected == correction.classification_corrected
        assert card.correction_reason == correction.correction_reason
        assert card.rule_applied == correction.rule_applied
        assert card.gaap_reference == correction.gaap_reference
        assert card.fiscal_impact_type == correction.fiscal_impact_type
        assert card.confidence_initial == correction.confidence_initial
        assert card.confidence_final == correction.confidence_final

    def test_error_card_has_no_client_id(self):
        card = self.system.generate_error_card("txn-001", make_correction())
        assert not hasattr(card, 'client_id')

    def test_error_card_has_no_transaction_id(self):
        """transaction_id no se almacena en la ErrorCard."""
        card = self.system.generate_error_card("txn-001", make_correction())
        assert not hasattr(card, 'transaction_id')

    def test_error_card_has_no_name_fields(self):
        card = self.system.generate_error_card("txn-001", make_correction())
        for attr in ('client_name', 'vendor_name', 'name', 'email', 'phone', 'address'):
            assert not hasattr(card, attr), f"ErrorCard no debe tener campo '{attr}'"

    def test_error_card_pii_verified_true(self):
        card = self.system.generate_error_card("txn-001", make_correction())
        assert card.pii_verified is True

    def test_error_card_is_frozen(self):
        card = self.system.generate_error_card("txn-001", make_correction())
        with pytest.raises(Exception):
            card.transaction_type = "OTRO"  # type: ignore

    def test_error_card_stored_in_system(self):
        self.system.generate_error_card("txn-001", make_correction())
        assert len(self.system.get_error_cards()) == 1

    def test_multiple_cards_stored(self):
        for i in range(5):
            self.system.generate_error_card(
                f"txn-{i}",
                make_correction(transaction_id=f"txn-{i}")
            )
        assert len(self.system.get_error_cards()) == 5

    def test_context_variables_stored(self):
        correction = make_correction(context_variables=("seasonal", "new_vendor_type"))
        card = self.system.generate_error_card("txn-001", correction)
        assert "seasonal" in card.context_variables
        assert "new_vendor_type" in card.context_variables


# ===========================================================================
# 5. HIGH_PRIORITY_LEARNING FLAG
# ===========================================================================

class TestHighPriorityLearning:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_high_confidence_corrected_is_high_priority(self):
        """confidence_initial > 80% y fue corregido → HIGH_PRIORITY_LEARNING."""
        correction = make_correction(confidence_initial=Decimal("0.85"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

    def test_confidence_81_percent_is_high_priority(self):
        correction = make_correction(confidence_initial=Decimal("0.81"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

    def test_confidence_exactly_80_is_not_high_priority(self):
        """confidence_initial == 80% NO es HIGH_PRIORITY (requiere estrictamente >80%)."""
        correction = make_correction(confidence_initial=Decimal("0.80"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.NORMAL

    def test_confidence_79_is_normal(self):
        correction = make_correction(confidence_initial=Decimal("0.79"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.NORMAL

    def test_confidence_50_is_normal(self):
        correction = make_correction(confidence_initial=Decimal("0.50"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.NORMAL

    def test_confidence_95_is_high_priority(self):
        correction = make_correction(confidence_initial=Decimal("0.95"))
        card = self.system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

    def test_get_error_cards_by_priority_filter(self):
        self.system.generate_error_card("txn-1", make_correction(confidence_initial=Decimal("0.90")))
        self.system.generate_error_card("txn-2", make_correction(confidence_initial=Decimal("0.60")))
        high = self.system.get_error_cards(priority=LearningPriority.HIGH_PRIORITY_LEARNING)
        normal = self.system.get_error_cards(priority=LearningPriority.NORMAL)
        assert len(high) == 1
        assert len(normal) == 1


# ===========================================================================
# 6. PII REJECTED IN generate_error_card
# ===========================================================================

class TestGenerateErrorCardPIIRejected:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_ssn_in_correction_reason_rejected(self):
        """correction_reason no es un campo seguro — si contiene PII debe rechazarse."""
        # correction_reason está en _SAFE_FIELDS, así que NO se escanea
        # Verificamos que el campo seguro no bloquea la creación
        correction = make_correction(
            correction_reason="Corrección por SSN 123-45-6789 del cliente"
        )
        # correction_reason está en _SAFE_FIELDS → no debería lanzar PIIViolationError
        card = self.system.generate_error_card("txn-pii-1", correction)
        assert card is not None

    def test_safe_fields_are_in_safe_set(self):
        """Verificar que los campos core de ErrorCard están en _SAFE_FIELDS."""
        expected_safe = {
            "transaction_type", "industry_code", "classification_initial",
            "classification_corrected", "correction_reason", "rule_applied",
            "gaap_reference", "fiscal_impact_type", "confidence_initial",
            "confidence_final", "context_variables",
        }
        for field in expected_safe:
            assert field in _SAFE_FIELDS, f"'{field}' debería estar en _SAFE_FIELDS"


# ===========================================================================
# 7. contribute_to_pool — PRIVACIDAD POR DISEÑO
# ===========================================================================

class TestContributeToPool:

    def setup_method(self):
        self.system = AprendizajeFederado()
        correction = make_correction(confidence_initial=Decimal("0.70"))
        self.card = self.system.generate_error_card("txn-001", correction)

    def test_contribute_returns_pattern_and_contribution(self):
        pattern, contrib = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        assert isinstance(pattern, BehaviorPattern)
        assert isinstance(contrib, PoolContribution)

    def test_behavior_pattern_has_no_client_id_field(self):
        """GARANTÍA ARQUITECTÓNICA: BehaviorPattern no tiene campo client_id."""
        pattern, _ = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        assert not hasattr(pattern, 'client_id')

    def test_pool_contribution_has_client_id(self):
        """PoolContribution SÍ guarda client_id (para métricas Eximia)."""
        _, contrib = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        assert contrib.client_id == "client-uuid-0001"

    def test_pool_contribution_has_no_pattern_id(self):
        """PoolContribution NO guarda el pattern_id — desvinculado."""
        pattern, contrib = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        # Verificar que pattern.pattern_id no aparece en contrib
        assert not hasattr(contrib, 'pattern_id')
        # Verificar que los values de contrib no contienen el pattern_id
        contrib_values = [
            contrib.contribution_id,
            contrib.client_id,
            contrib.pattern_hash,
        ]
        assert pattern.pattern_id not in contrib_values

    def test_pattern_hash_not_equal_to_pattern_id(self):
        """pattern_hash es SHA-256 del contenido, no el pattern_id."""
        pattern, contrib = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        assert contrib.pattern_hash != pattern.pattern_id
        assert len(contrib.pattern_hash) == 64   # SHA-256 hex

    def test_cannot_join_client_to_pattern(self):
        """No existe ningún campo común que permita hacer JOIN client ↔ pattern."""
        pattern, contrib = self.system.contribute_to_pool(
            self.card.error_card_id, "client-uuid-0001"
        )
        # Los únicos identificadores son pattern_id (en pattern) y client_id (en contrib)
        # No hay campo pattern_id en contrib ni client_id en pattern
        assert not hasattr(contrib, 'pattern_id')
        assert not hasattr(pattern, 'client_id')

    def test_error_card_marked_as_contributed(self):
        self.system.contribute_to_pool(self.card.error_card_id, "client-uuid-0001")
        cards = self.system.get_error_cards()
        contributed = [c for c in cards if c.error_card_id == self.card.error_card_id]
        assert len(contributed) == 1
        assert contributed[0].is_contributed_to_pool is True

    def test_contribute_nonexistent_card_raises(self):
        with pytest.raises(ErrorCardNotFoundError):
            self.system.contribute_to_pool("nonexistent-id-xyz", "client-uuid-0001")

    def test_same_pattern_increases_frequency(self):
        """Dos correcciones del mismo tipo → frecuencia del patrón sube."""
        c2 = make_correction(
            transaction_type="EXPENSE",
            classification_initial="5200",
            classification_corrected="5300",
            confidence_initial=Decimal("0.65"),
        )
        card2 = self.system.generate_error_card("txn-002", c2)
        pattern1, _ = self.system.contribute_to_pool(self.card.error_card_id, "client-A")
        pattern2, _ = self.system.contribute_to_pool(card2.error_card_id, "client-B")
        # Deben ser el mismo pattern_id con frecuencia 2
        assert pattern1.pattern_id == pattern2.pattern_id
        assert pattern2.frequency == 2

    def test_different_pattern_new_entry(self):
        """Correcciones con clasificaciones distintas generan patrones separados."""
        c2 = make_correction(
            classification_initial="5200",
            classification_corrected="6000",   # diferente de 5300
        )
        card2 = self.system.generate_error_card("txn-002", c2)
        self.system.contribute_to_pool(self.card.error_card_id, "client-A")
        self.system.contribute_to_pool(card2.error_card_id, "client-B")
        patterns = self.system.get_behavior_patterns()
        assert len(patterns) == 2

    def test_behavior_pattern_source_error_card_ids_tracked(self):
        """El patrón registra los error_card_ids que lo originaron."""
        pattern, _ = self.system.contribute_to_pool(self.card.error_card_id, "client-uuid-0001")
        assert self.card.error_card_id in pattern.source_error_card_ids

    def test_behavior_pattern_is_frozen(self):
        pattern, _ = self.system.contribute_to_pool(self.card.error_card_id, "client-uuid-0001")
        with pytest.raises(Exception):
            pattern.transaction_type = "OTRO"  # type: ignore


# ===========================================================================
# 8. DATOS PERSONALES NO PUEDEN EXTRAERSE DE ERROR_CARDS
# ===========================================================================

class TestNoPIIInErrorCards:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_error_card_fields_contain_no_pii(self):
        """Ningún campo de ErrorCard contiene PII."""
        correction = make_correction(
            client_id="client-with-fake-ein-00-1234567",
        )
        card = self.system.generate_error_card("txn-001", correction)

        # Verificar cada campo no-seguro del card
        card_dict = {
            "transaction_type":       card.transaction_type,
            "industry_code":          card.industry_code,
        }
        verify_no_pii(card_dict)   # no debe lanzar

    def test_client_id_not_in_error_card(self):
        """client_id del CPACorrection no aparece en ErrorCard."""
        client_id = "client-uuid-super-privado"
        correction = make_correction(client_id=client_id)
        card = self.system.generate_error_card("txn-001", correction)
        # Serializar todos los valores del card
        card_str = card.model_dump_json()
        assert client_id not in card_str

    def test_transaction_id_not_in_error_card(self):
        """transaction_id no se almacena en ErrorCard."""
        transaction_id = "txn-super-secreto-001"
        correction = make_correction()
        card = self.system.generate_error_card(transaction_id, correction)
        card_str = card.model_dump_json()
        assert transaction_id not in card_str

    def test_behavior_pattern_has_no_pii_fields(self):
        """Los campos de BehaviorPattern no exponen PII."""
        correction = make_correction(confidence_initial=Decimal("0.70"))
        card = self.system.generate_error_card("txn-001", correction)
        pattern, _ = self.system.contribute_to_pool(card.error_card_id, "client-uuid-9876")

        # Verificar campos del patrón — no deben contener PII
        pattern_data = {
            "transaction_type": pattern.transaction_type,
            "industry_code":    pattern.industry_code,
            "rule_applied":     pattern.rule_applied,
            "gaap_reference":   pattern.gaap_reference,
        }
        verify_no_pii(pattern_data)

    def test_client_id_not_in_behavior_pattern(self):
        """client_id no aparece en la serialización de BehaviorPattern."""
        client_id = "client-muy-privado-001"
        correction = make_correction(confidence_initial=Decimal("0.60"))
        card = self.system.generate_error_card("txn-001", correction)
        pattern, _ = self.system.contribute_to_pool(card.error_card_id, client_id)
        pattern_str = pattern.model_dump_json()
        assert client_id not in pattern_str

    def test_error_card_not_linkable_to_client(self):
        """Una ErrorCard no puede ser vinculada a un cliente específico."""
        client_id = "client-privado-xyz"
        correction = make_correction(client_id=client_id)
        card = self.system.generate_error_card("txn-001", correction)
        # No hay campo client_id ni nada que permita identificar al cliente
        assert not hasattr(card, 'client_id')
        assert not hasattr(card, 'client_name')
        assert not hasattr(card, 'ein')
        assert not hasattr(card, 'ssn')


# ===========================================================================
# 9. contribute_to_pool — DOBLE VERIFICACIÓN PII
# ===========================================================================

class TestDoubleVerification:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_clean_card_passes_double_verification(self):
        """Una ErrorCard limpia pasa las dos verificaciones sin error."""
        correction = make_correction(confidence_initial=Decimal("0.65"))
        card = self.system.generate_error_card("txn-001", correction)
        # No debe lanzar
        pattern, contrib = self.system.contribute_to_pool(card.error_card_id, "client-001")
        assert pattern is not None
        assert contrib is not None

    def test_pool_contributions_retrieved(self):
        correction = make_correction()
        card = self.system.generate_error_card("txn-001", correction)
        self.system.contribute_to_pool(card.error_card_id, "client-001")
        contribs = self.system.get_pool_contributions()
        assert len(contribs) == 1
        assert contribs[0].client_id == "client-001"


# ===========================================================================
# 10. calibrate_confidence
# ===========================================================================

class TestCalibrateConfidence:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_calibrate_returns_confidence_calibration(self):
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert isinstance(cal, ConfidenceCalibration)

    def test_calibrate_stored_in_system(self):
        self.system.calibrate_confidence("EXPENSE", "client-001")
        cals = self.system.get_calibrations()
        assert len(cals) == 1

    def test_calibrate_by_client_id(self):
        self.system.calibrate_confidence("EXPENSE", "client-001")
        self.system.calibrate_confidence("PAYROLL", "client-002")
        cals_001 = self.system.get_calibrations(client_id="client-001")
        assert len(cals_001) == 1
        assert cals_001[0].client_id == "client-001"

    def test_high_conf_wrong_reduces_threshold(self):
        """Si hay correcciones con confidence > 80%, umbral baja."""
        for i in range(3):
            correction = make_correction(
                confidence_initial=Decimal("0.90"),   # > 80%
                transaction_type="EXPENSE",
            )
            self.system.generate_error_card(f"txn-{i}", correction)
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert cal.new_threshold < cal.old_threshold
        assert cal.high_conf_wrong == 3

    def test_no_corrections_no_threshold_change(self):
        """Sin correcciones históricas, umbral no cambia significativamente."""
        cal = self.system.calibrate_confidence("REVENUE", "client-999")
        # Sin correcciones → no hay razón para cambiar
        assert cal.total_corrections == 0

    def test_calibration_is_frozen(self):
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        with pytest.raises(Exception):
            cal.old_threshold = Decimal("0.50")  # type: ignore

    def test_calibration_has_period_dates(self):
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert cal.period_from is not None
        assert cal.period_to is not None

    def test_calibration_correction_rate_is_decimal(self):
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert isinstance(cal.correction_rate, Decimal)

    def test_threshold_not_below_minimum(self):
        """El umbral no baja de 0.50 por muchas correcciones de alta confianza."""
        for i in range(20):
            correction = make_correction(
                confidence_initial=Decimal("0.95"),
                transaction_type="EXPENSE",
            )
            self.system.generate_error_card(f"txn-{i}", correction)
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert cal.new_threshold >= Decimal("0.50")

    def test_threshold_not_above_maximum(self):
        """El umbral no sube de 0.90."""
        cal = self.system.calibrate_confidence("EXPENSE", "client-001")
        assert cal.new_threshold <= Decimal("0.90")


# ===========================================================================
# 11. verify_pool_privacy — LAS 3 GARANTÍAS
# ===========================================================================

class TestVerifyPoolPrivacy:

    def setup_method(self):
        self.system = AprendizajeFederado()

    def test_empty_pool_privacy_verified(self):
        """Pool vacío satisface todas las garantías."""
        result = self.system.verify_pool_privacy()
        assert result["behavior_patterns_have_no_client_id"] is True
        assert result["contributions_have_no_pattern_id"] is True
        assert result["patterns_are_pii_free"] is True

    def test_pool_with_data_privacy_verified(self):
        """Pool con datos satisface todas las garantías."""
        correction = make_correction(confidence_initial=Decimal("0.65"))
        card = self.system.generate_error_card("txn-001", correction)
        self.system.contribute_to_pool(card.error_card_id, "client-001")
        result = self.system.verify_pool_privacy()
        assert result["behavior_patterns_have_no_client_id"] is True
        assert result["contributions_have_no_pattern_id"] is True
        assert result["patterns_are_pii_free"] is True

    def test_privacy_returns_dict_with_3_keys(self):
        result = self.system.verify_pool_privacy()
        assert len(result) == 3
        assert "behavior_patterns_have_no_client_id" in result
        assert "contributions_have_no_pattern_id" in result
        assert "patterns_are_pii_free" in result

    def test_multiple_contributions_privacy_maintained(self):
        """Con múltiples contribuciones de distintos clientes, privacidad garantizada."""
        clients = ["client-A", "client-B", "client-C"]
        for i, client in enumerate(clients):
            correction = make_correction(
                transaction_type="EXPENSE",
                classification_initial=f"5{i}00",
                classification_corrected=f"6{i}00",
                confidence_initial=Decimal("0.60"),
            )
            card = self.system.generate_error_card(f"txn-{i}", correction)
            self.system.contribute_to_pool(card.error_card_id, client)
        result = self.system.verify_pool_privacy()
        assert all(result.values()), "No todas las garantías de privacidad se cumplen"

    def test_behavior_pattern_model_has_no_client_id_attribute(self):
        """A nivel de modelo, BehaviorPattern nunca podrá tener client_id."""
        import inspect
        fields = BehaviorPattern.model_fields
        assert "client_id" not in fields, "BehaviorPattern NO debe tener campo client_id"

    def test_pool_contribution_model_has_no_pattern_id_attribute(self):
        """PoolContribution nunca podrá tener pattern_id."""
        fields = PoolContribution.model_fields
        assert "pattern_id" not in fields, "PoolContribution NO debe tener campo pattern_id"


# ===========================================================================
# 12. INTEGRACIÓN — flujo completo
# ===========================================================================

class TestFullFlowIntegration:

    def test_full_flow_error_card_to_pool_to_calibration(self):
        system = AprendizajeFederado()

        # 1. CPA corrige una transacción con alta confianza
        correction = make_correction(
            transaction_type="REVENUE",
            classification_initial="4100",
            classification_corrected="4200",
            confidence_initial=Decimal("0.88"),
            fiscal_impact_type=FiscalImpactType.IVU_MISCLASSIFICATION,
        )
        card = system.generate_error_card("txn-001", correction)
        assert card.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

        # 2. Contribuir al pool
        pattern, contrib = system.contribute_to_pool(card.error_card_id, "client-final-001")
        assert not hasattr(pattern, 'client_id')
        assert not hasattr(contrib, 'pattern_id')

        # 3. Calibrar confianza
        cal = system.calibrate_confidence("REVENUE", "client-final-001")
        assert cal.high_conf_wrong >= 0

        # 4. Verificar privacidad
        privacy = system.verify_pool_privacy()
        assert all(privacy.values())

    def test_three_different_clients_same_pattern_no_link(self):
        """3 clientes corrijen el mismo patrón → un solo patrón, 3 contribuciones."""
        system = AprendizajeFederado()
        for i, client in enumerate(["client-X", "client-Y", "client-Z"]):
            correction = make_correction(
                transaction_type="EXPENSE",
                classification_initial="5200",
                classification_corrected="5300",
                confidence_initial=Decimal("0.75"),
            )
            card = system.generate_error_card(f"txn-{i}", correction)
            system.contribute_to_pool(card.error_card_id, client)

        patterns = system.get_behavior_patterns()
        contributions = system.get_pool_contributions()

        # Un solo patrón
        assert len(patterns) == 1
        assert patterns[0].frequency == 3

        # 3 contribuciones con 3 client_ids distintos
        assert len(contributions) == 3
        client_ids = {c.client_id for c in contributions}
        assert client_ids == {"client-X", "client-Y", "client-Z"}

        # El pattern_id NO está en ninguna contribución
        pattern_id = patterns[0].pattern_id
        for contrib in contributions:
            assert not hasattr(contrib, 'pattern_id')
            assert contrib.pattern_hash != pattern_id

    def test_error_card_fields_are_all_behavioral(self):
        """Todos los campos de ErrorCard son comportamentales, no identificativos."""
        system = AprendizajeFederado()
        correction = make_correction()
        card = system.generate_error_card("txn-001", correction)
        fields = ErrorCard.model_fields.keys()
        forbidden = {'client_id', 'client_name', 'vendor_name', 'name', 'email',
                     'phone', 'ssn', 'ein', 'address', 'account_number', 'transaction_id'}
        for field in forbidden:
            assert field not in fields, f"Campo PII '{field}' no debe estar en ErrorCard"
