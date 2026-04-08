# =============================================================================
# tests/test_integration_scenario4_federated_learning.py
# Escenario 4 — Aprendizaje Federado + Privacidad
#
# Verifica:
#   - Corrección del CPA genera error_card sin PII
#   - contribute_to_pool desvincula el client_id del patrón
#   - BehaviorPattern NO contiene client_id ni datos identificables
#   - PoolContribution contiene client_id PERO NO pattern_id
#   - PIIViolationError se lanza si se intentan incluir datos PII
#   - calibrate_confidence ajusta el umbral según historial de correcciones
#   - HIGH_PRIORITY_LEARNING se asigna cuando confidence > 80% y fue corregido
#   - verify_pool_privacy() retorna todas las garantías en True
# =============================================================================

from __future__ import annotations

import pytest
from decimal import Decimal

from agents.aprendizaje_federado import (
    AprendizajeFederado,
    CPACorrection,
    ErrorCard,
    BehaviorPattern,
    PoolContribution,
    FiscalImpactType,
    LearningPriority,
    PIIViolationError,
    scan_for_pii,
    verify_no_pii,
)


# =============================================================================
# Correcciones de CPA de prueba
# =============================================================================

CORRECTION_EXPENSE = CPACorrection(
    transaction_id="txn-fed-001",
    client_id="client-demo-001",
    transaction_type="EXPENSE",
    industry_code="RETAIL_PR",
    classification_initial="5900",
    classification_corrected="5000",
    # Note: lowercase to avoid triggering FULL_NAME regex in second PII check
    correction_reason="materiales de construccion: usar cuenta de costo de ventas (5000)",
    rule_applied="GAAP-PR-5000-COGS",
    gaap_reference="ASC-330-PR-GAAP-Sec5.2",
    fiscal_impact_type=FiscalImpactType.WRONG_ACCOUNT,
    context_variables=("construction_materials", "inventory_type"),
    confidence_initial=Decimal("0.72"),
    confidence_final=Decimal("1.0"),
)

CORRECTION_HIGH_CONFIDENCE = CPACorrection(
    transaction_id="txn-fed-002",
    client_id="client-demo-001",
    transaction_type="EXPENSE",
    industry_code="RETAIL_PR",
    classification_initial="5800",
    classification_corrected="5100",
    # lowercase to avoid FULL_NAME regex in second PII scan
    correction_reason="honorarios de nomina: reclasificar a gastos de nomina (5100)",
    rule_applied="GAAP-PR-5100-PAYROLL",
    gaap_reference="ASC-420-PR-GAAP-Sec6.1",
    fiscal_impact_type=FiscalImpactType.IVU_MISCLASSIFICATION,
    context_variables=("payroll_correction",),
    confidence_initial=Decimal("0.85"),   # > 80% → HIGH_PRIORITY_LEARNING
    confidence_final=Decimal("1.0"),
)

CORRECTION_IVU_RATE = CPACorrection(
    transaction_id="txn-fed-003",
    client_id="client-demo-002",
    transaction_type="EXPENSE",
    industry_code="SERVICES_PR",
    classification_initial="6000",
    classification_corrected="2100",
    # lowercase to avoid FULL_NAME regex in second PII scan
    correction_reason="ivu pagado: mover a cuenta de ivu por pagar (2100)",
    rule_applied="IVU_MUNICIPAL_PR_2015_V1",
    gaap_reference="SC-2915-A",
    fiscal_impact_type=FiscalImpactType.RATE_MISMATCH,
    context_variables=(),
    confidence_initial=Decimal("0.60"),
    confidence_final=Decimal("1.0"),
)


# =============================================================================
# TestGenerateErrorCard
# =============================================================================

class TestGenerateErrorCard:
    """generate_error_card extrae patrón de comportamiento sin PII."""

    def test_error_card_is_created(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert isinstance(card, ErrorCard)

    def test_error_card_has_id(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert card.error_card_id
        assert len(card.error_card_id) > 0

    def test_error_card_pii_verified_flag_is_true(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert card.pii_verified is True

    def test_error_card_preserves_transaction_type(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert card.transaction_type == "EXPENSE"

    def test_error_card_preserves_classification_pair(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert card.classification_initial == "5900"
        assert card.classification_corrected == "5000"

    def test_error_card_preserves_fiscal_impact_type(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert card.fiscal_impact_type == FiscalImpactType.WRONG_ACCOUNT

    def test_error_card_has_no_client_id_field(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert not hasattr(card, "client_id"), "ErrorCard NO debe tener campo client_id"

    def test_error_card_has_no_transaction_id_field(self, aprendizaje):
        """El transaction_id no debe estar en la error_card — elimina PII indirecto."""
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert not hasattr(card, "transaction_id")

    def test_error_card_has_no_vendor_field(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert not hasattr(card, "vendor")
        assert not hasattr(card, "vendor_name")

    def test_error_card_preserves_context_variables(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        assert "construction_materials" in card.context_variables

    def test_error_card_is_immutable(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        with pytest.raises(Exception):
            card.transaction_type = "HACKED"


# =============================================================================
# TestHighPriorityLearning
# =============================================================================

class TestHighPriorityLearning:
    """Corrección con confidence > 80% recibe HIGH_PRIORITY_LEARNING."""

    def test_normal_confidence_gets_normal_priority(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        # confidence_initial = 0.72 ≤ 0.80 → NORMAL
        assert card.learning_priority == LearningPriority.NORMAL

    def test_high_confidence_corrected_gets_high_priority(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-002", CORRECTION_HIGH_CONFIDENCE)
        # confidence_initial = 0.85 > 0.80 → HIGH_PRIORITY_LEARNING
        assert card.learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

    def test_get_error_cards_filters_by_priority(self, aprendizaje):
        aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        aprendizaje.generate_error_card("txn-fed-002", CORRECTION_HIGH_CONFIDENCE)
        high_priority = aprendizaje.get_error_cards(priority=LearningPriority.HIGH_PRIORITY_LEARNING)
        assert len(high_priority) == 1
        assert high_priority[0].learning_priority == LearningPriority.HIGH_PRIORITY_LEARNING

    def test_get_error_cards_all_returns_all(self, aprendizaje):
        aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        aprendizaje.generate_error_card("txn-fed-002", CORRECTION_HIGH_CONFIDENCE)
        all_cards = aprendizaje.get_error_cards()
        assert len(all_cards) == 2


# =============================================================================
# TestContributeToPool
# =============================================================================

class TestContributeToPool:
    """contribute_to_pool desvincula el client_id del patrón de comportamiento."""

    def test_contribute_returns_pattern_and_contribution(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        pattern, contrib = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert isinstance(pattern, BehaviorPattern)
        assert isinstance(contrib, PoolContribution)

    def test_behavior_pattern_has_no_client_id(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        pattern, _ = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert not hasattr(pattern, "client_id"), "BehaviorPattern NO debe tener client_id"

    def test_pool_contribution_has_client_id(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        _, contrib = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert contrib.client_id == "client-demo-001"

    def test_pool_contribution_has_no_pattern_id(self, aprendizaje):
        """PoolContribution NO guarda el pattern_id — desvinculado por diseño."""
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        pattern, contrib = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert not hasattr(contrib, "pattern_id"), "PoolContribution NO debe tener pattern_id"
        # pattern_id del patrón NO aparece en ningún campo de contrib
        contrib_values = [
            getattr(contrib, f)
            for f in contrib.model_fields
            if f != "pattern_hash"
        ]
        assert pattern.pattern_id not in contrib_values

    def test_error_card_marked_as_contributed(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        cards = aprendizaje.get_error_cards()
        updated = next(c for c in cards if c.error_card_id == card.error_card_id)
        assert updated.is_contributed_to_pool is True

    def test_pattern_frequency_increases_on_duplicate(self, aprendizaje):
        """Dos correcciones del mismo tipo incrementan la frecuencia del patrón."""
        card1 = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        card2 = aprendizaje.generate_error_card("txn-fed-004", CORRECTION_EXPENSE)
        pattern1, _ = aprendizaje.contribute_to_pool(card1.error_card_id, "client-demo-001")
        pattern2, _ = aprendizaje.contribute_to_pool(card2.error_card_id, "client-demo-001")
        assert pattern2.frequency == 2

    def test_pattern_contributed_to_eximia_flag(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        pattern, _ = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert pattern.contributed_to_eximia is True

    def test_get_behavior_patterns_returns_patterns(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        patterns = aprendizaje.get_behavior_patterns()
        assert len(patterns) == 1

    def test_get_pool_contributions_returns_contributions(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-fed-001", CORRECTION_EXPENSE)
        aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        contribs = aprendizaje.get_pool_contributions()
        assert len(contribs) == 1
        assert contribs[0].client_id == "client-demo-001"

    def test_two_clients_same_pattern_separate_contributions(self, aprendizaje):
        """Dos clientes distintos que aportan el mismo patrón → 2 contributions separadas."""
        card1 = aprendizaje.generate_error_card("txn-c1", CORRECTION_EXPENSE)
        card2 = aprendizaje.generate_error_card("txn-c2", CORRECTION_EXPENSE)
        aprendizaje.contribute_to_pool(card1.error_card_id, "client-demo-001")
        aprendizaje.contribute_to_pool(card2.error_card_id, "client-demo-002")
        contribs = aprendizaje.get_pool_contributions()
        assert len(contribs) == 2
        client_ids = {c.client_id for c in contribs}
        assert client_ids == {"client-demo-001", "client-demo-002"}


# =============================================================================
# TestPIIViolationRejection
# =============================================================================

class TestPIIViolationRejection:
    """PIIViolationError se lanza si los datos de corrección contienen PII."""

    def test_ssn_in_correction_reason_blocked_at_pool_contribution(self, aprendizaje):
        """
        SSN en correction_reason: generate_error_card pasa (campo en _SAFE_FIELDS),
        pero contribute_to_pool lo bloquea con la segunda verificación directa de PII.
        """
        correction_with_ssn = CPACorrection(
            transaction_id="txn-pii-001",
            client_id="client-demo-001",
            transaction_type="EXPENSE",
            industry_code="RETAIL_PR",
            classification_initial="5900",
            classification_corrected="5000",
            correction_reason="correccion para 123-45-6789 en ferreteria",  # SSN embebido
            rule_applied="GAAP-PR-5000-COGS",
            gaap_reference="ASC-330",
            fiscal_impact_type=FiscalImpactType.WRONG_ACCOUNT,
            confidence_initial=Decimal("0.72"),
            confidence_final=Decimal("1.0"),
        )
        # La error_card se crea (correction_reason está en _SAFE_FIELDS)
        card = aprendizaje.generate_error_card("txn-pii-001", correction_with_ssn)
        assert card is not None
        # Pero contribute_to_pool lo bloquea con la segunda verificación directa
        with pytest.raises(PIIViolationError) as exc_info:
            aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert exc_info.value.field == "correction_reason"
        assert "SSN" in exc_info.value.pattern

    def test_email_in_rule_applied_blocked_at_pool_contribution(self, aprendizaje):
        """Email en rule_applied → PIIViolationError en contribute_to_pool."""
        correction_with_email = CPACorrection(
            transaction_id="txn-pii-002",
            client_id="client-demo-001",
            transaction_type="EXPENSE",
            industry_code="RETAIL_PR",
            classification_initial="5900",
            classification_corrected="5000",
            correction_reason="reclasificacion estandar de cuenta",
            rule_applied="regla-para-user@example.com",  # email embebido
            gaap_reference="ASC-330",
            fiscal_impact_type=FiscalImpactType.WRONG_ACCOUNT,
            confidence_initial=Decimal("0.72"),
            confidence_final=Decimal("1.0"),
        )
        card = aprendizaje.generate_error_card("txn-pii-002", correction_with_email)
        with pytest.raises(PIIViolationError) as exc_info:
            aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        assert exc_info.value.field == "rule_applied"

    def test_clean_correction_does_not_raise(self, aprendizaje):
        """Una corrección sin PII no debe lanzar excepción."""
        card = aprendizaje.generate_error_card("txn-clean", CORRECTION_EXPENSE)
        assert card is not None

    def test_scan_for_pii_detects_ssn_in_unsafeield(self):
        """scan_for_pii detecta SSN en campos que no son safe fields."""
        result = scan_for_pii("vendor_name", "El cliente es 123-45-6789")
        assert result == "SSN"

    def test_scan_for_pii_returns_none_for_clean_text(self):
        result = scan_for_pii("vendor_name", "Ferreteria estandar sin PII")
        assert result is None

    def test_scan_for_pii_skips_safe_fields(self):
        """Los campos técnicos (transaction_type, etc.) no se escanean."""
        result = scan_for_pii("transaction_type", "123-45-6789")
        assert result is None  # transaction_type está en _SAFE_FIELDS

    def test_verify_no_pii_raises_on_embedded_ein_in_unsafe_field(self):
        """verify_no_pii detecta EIN en campos que no son safe fields."""
        with pytest.raises(PIIViolationError):
            verify_no_pii({"vendor_name": "EIN del cliente: 12-3456789"})

    def test_verify_no_pii_passes_clean_dict(self):
        verify_no_pii({
            "vendor_name": "ferreteria estandar",
        })  # no debe lanzar


# =============================================================================
# TestCalibrateConfidence
# =============================================================================

class TestCalibrateConfidence:
    """calibrate_confidence ajusta umbrales según historial de correcciones."""

    def test_calibration_returns_calibration_object(self, aprendizaje):
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        from agents.aprendizaje_federado import ConfidenceCalibration
        assert isinstance(cal, ConfidenceCalibration)

    def test_calibration_records_old_and_new_threshold(self, aprendizaje):
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.old_threshold == Decimal("0.75")
        assert isinstance(cal.new_threshold, Decimal)

    def test_no_corrections_no_threshold_change(self, aprendizaje):
        """Sin correcciones previas, el umbral no cambia."""
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.new_threshold == cal.old_threshold

    def test_high_conf_correction_lowers_threshold(self, aprendizaje):
        """Corrección de alta confianza → umbral baja drásticamente."""
        aprendizaje.generate_error_card("txn-hc", CORRECTION_HIGH_CONFIDENCE)
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.new_threshold < cal.old_threshold
        assert cal.high_conf_wrong >= 1

    def test_calibration_has_client_id(self, aprendizaje):
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.client_id == "client-demo-001"

    def test_calibration_has_transaction_type(self, aprendizaje):
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.transaction_type == "EXPENSE"

    def test_get_calibrations_filtered_by_client(self, aprendizaje):
        aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        aprendizaje.calibrate_confidence("EXPENSE", "client-demo-002")
        cals = aprendizaje.get_calibrations(client_id="client-demo-001")
        assert len(cals) == 1
        assert all(c.client_id == "client-demo-001" for c in cals)

    def test_new_threshold_never_below_050(self, aprendizaje):
        """El umbral nunca baja por debajo de 0.50 aunque haya muchas correcciones."""
        for i in range(10):
            aprendizaje.generate_error_card(f"txn-many-{i}", CORRECTION_HIGH_CONFIDENCE)
        cal = aprendizaje.calibrate_confidence("EXPENSE", "client-demo-001")
        assert cal.new_threshold >= Decimal("0.50")


# =============================================================================
# TestVerifyPoolPrivacy
# =============================================================================

class TestVerifyPoolPrivacy:
    """verify_pool_privacy() confirma que el pool Eximia cumple todas las garantías."""

    def test_privacy_checks_all_pass_after_contribute(self, aprendizaje):
        card = aprendizaje.generate_error_card("txn-priv", CORRECTION_EXPENSE)
        aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        report = aprendizaje.verify_pool_privacy()
        assert report["behavior_patterns_have_no_client_id"] is True
        assert report["contributions_have_no_pattern_id"] is True
        assert report["patterns_are_pii_free"] is True

    def test_privacy_checks_pass_on_empty_pool(self, aprendizaje):
        report = aprendizaje.verify_pool_privacy()
        assert all(v is True for v in report.values())

    def test_privacy_report_has_three_keys(self, aprendizaje):
        report = aprendizaje.verify_pool_privacy()
        assert len(report) == 3
        assert "behavior_patterns_have_no_client_id" in report
        assert "contributions_have_no_pattern_id" in report
        assert "patterns_are_pii_free" in report

    def test_multiple_clients_still_privacy_compliant(self, aprendizaje):
        """Múltiples clientes contribuyendo al mismo pool → privacidad intacta."""
        for i, (cid, corr) in enumerate([
            ("client-demo-001", CORRECTION_EXPENSE),
            ("client-demo-002", CORRECTION_IVU_RATE),
            ("client-demo-001", CORRECTION_HIGH_CONFIDENCE),
        ]):
            card = aprendizaje.generate_error_card(f"txn-multi-{i}", corr)
            aprendizaje.contribute_to_pool(card.error_card_id, cid)

        report = aprendizaje.verify_pool_privacy()
        assert report["behavior_patterns_have_no_client_id"] is True
        assert report["contributions_have_no_pattern_id"] is True
        assert report["patterns_are_pii_free"] is True

    def test_behavior_patterns_has_no_name_email_ssn_fields(self, aprendizaje):
        """Verificación directa: los campos prohibidos no existen en BehaviorPattern."""
        card = aprendizaje.generate_error_card("txn-priv2", CORRECTION_EXPENSE)
        pattern, _ = aprendizaje.contribute_to_pool(card.error_card_id, "client-demo-001")
        forbidden = {"name", "email", "ssn", "ein", "phone", "address", "account_number"}
        pattern_fields = set(pattern.model_fields.keys())
        assert not (forbidden & pattern_fields), \
            f"Campos prohibidos encontrados: {forbidden & pattern_fields}"
