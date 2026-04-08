# =============================================================================
# tests/conftest.py
# Fixtures compartidos para la suite de integración de Bit-Counting.
#
# Provee:
#   - redis_client      : fakeredis aislado por test
#   - orchestrator      : OrchestratorV2 con vision mock
#   - flow_coordinator  : FlowCoordinator reutilizable
#   - centinela         : CentinelaGuardian con config demo
#   - aprendizaje       : AprendizajeFederado
#   - api_client        : FastAPI TestClient
#   - auth headers      : _client_h, _cpa_h, _admin_h
#   - Documentos demo   : CLEAN_INVOICE, MISSING_AMOUNT_DOC, etc.
# =============================================================================

from __future__ import annotations

import base64
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from decimal import Decimal

import fakeredis
import pytest
from fastapi.testclient import TestClient

from agents.aprendizaje_federado import AprendizajeFederado
from agents.centinela_guardian import CentinelaGuardian, ClientConfig
from agents.flow import FlowCoordinator
from agents.orchestrator_v2 import OrchestratorV2
from api.auth import Role, create_test_token
from api.main import app


# =============================================================================
# VISION CLIENT MOCKS
# =============================================================================

class GoodVisionClient:
    """Factura completa de materiales en PR — todos los campos presentes."""
    RESPONSE = {
        "vendor":        "Ferretería San Juan LLC",
        "date":          "2026-03-15",
        "amount":        "2450.00",
        "tax_amount":    "281.75",
        "currency":      "USD",
        "payment_method": "CHECK",
        "document_type": "INVOICE",
        "line_items": [
            {"description": "Materiales de construcción – tubería PVC", "amount": "1200.00"},
            {"description": "Cemento portland 50 sacos",                "amount": "1250.00"},
        ],
    }
    def extract(self, _bytes, _fmt):
        return self.RESPONSE


class LowConfidenceVisionClient:
    """Documento ilegible — vendor, tax_amount y payment_method faltantes."""
    RESPONSE = {
        "vendor":        None,
        "date":          "2026-03-15",
        "amount":        "2450.00",
        "tax_amount":    None,
        "currency":      "USD",
        "payment_method": None,
        "document_type": "INVOICE",
        "line_items":    [],
    }
    def extract(self, _bytes, _fmt):
        return self.RESPONSE


class MissingCriticalVisionClient:
    """Imagen borrosa — monto crítico ausente."""
    RESPONSE = {
        "vendor":        "Proveedor Desconocido",
        "date":          "2026-03-15",
        "amount":        None,            # CAMPO CRÍTICO AUSENTE
        "tax_amount":    None,
        "currency":      "USD",
        "payment_method": None,
        "document_type": "INVOICE",
        "line_items":    [],
    }
    def extract(self, _bytes, _fmt):
        return self.RESPONSE


class FailingVisionClient:
    """Simula fallo del servicio de visión."""
    def extract(self, _bytes, _fmt):
        raise RuntimeError("Vision API unavailable — connection refused")


# =============================================================================
# DOCUMENTOS DE PRUEBA
# =============================================================================

CLEAN_INVOICE_RAW = {
    "vendor":               "Ferretería San Juan LLC",
    "date":                 "2026-03-15",
    "amount":               Decimal("2450.00"),
    "tax_amount":           Decimal("281.75"),   # 11.5 % de $2450
    "currency":             "USD",
    "payment_method":       "CHECK",
    "confidence":           Decimal("0.91"),
    "missing_fields":       [],
    "critical_fields_missing": False,
}

MISSING_AMOUNT_RAW = {
    "vendor":               "Proveedor Sin Monto",
    "date":                 "2026-03-20",
    "amount":               None,
    "tax_amount":           None,
    "currency":             "USD",
    "payment_method":       None,
    "confidence":           Decimal("0.35"),
    "missing_fields":       ["amount"],
    "critical_fields_missing": True,
}

LOW_CONFIDENCE_RAW = {
    "vendor":               None,
    "date":                 "2026-03-18",
    "amount":               Decimal("500.00"),
    "tax_amount":           None,
    "currency":             "USD",
    "payment_method":       None,
    "confidence":           Decimal("0.45"),
    "missing_fields":       ["vendor", "tax_amount", "payment_method"],
    "critical_fields_missing": False,
}

IMBALANCED_INVOICE_RAW = {
    "vendor":               "Proveedora Incorrecta SA",
    "date":                 "2026-03-10",
    "amount":               Decimal("1000.00"),
    "tax_amount":           Decimal("999.00"),   # IVU = 99.9% — imposible
    "currency":             "USD",
    "payment_method":       "CASH",
    "confidence":           Decimal("0.91"),
    "missing_fields":       [],
    "critical_fields_missing": False,
}

# Documento en bytes para OrchestratorV2
CLEAN_INVOICE_BYTES = b"FACTURA\nFerreteria San Juan LLC\nFecha: 2026-03-15\nTotal: $2450.00\nIVU: $281.75"


# =============================================================================
# HISTORIAL DE TRANSACCIONES DEMO
# =============================================================================

# Historial que activa el trigger RULE_CONTRADICTION_90D:
# La regla nueva contradice lo aplicado en los últimos 90 días.
HISTORY_90D_WITH_OLD_RULE = (
    {
        "transaction_id":  "prev-100",
        "vendor":          "Ferretería San Juan LLC",
        "transaction_type": "EXPENSE",
        "rule_id":         "IVU_MUNICIPAL_PR_2015_V1",   # regla antigua
        "amount":          "1000.00",
        "date":            "2025-12-20",
        "confidence":      0.88,
    },
)

HISTORY_6M_PRECEDENT = (
    {
        "transaction_id":  "prev-200",
        "vendor":          "Ferretería San Juan LLC",
        "transaction_type": "EXPENSE",
        "rule_id":         "IVU_ESTATAL_PR_2015_V1",
        "amount":          "800.00",
        "date":            "2025-10-10",
        "confidence":      0.92,
    },
)

CLIENT_CONFIG_DEMO = ClientConfig(
    client_id="client-demo-001",
    confidence_threshold=Decimal("0.75"),
    sla_hours_default=48,
    assigned_cpa_license="CPA-PR-001234",
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture()
def redis_client():
    """fakeredis aislado — limpio por cada test."""
    return fakeredis.FakeRedis()


@pytest.fixture()
def orchestrator(redis_client):
    """OrchestratorV2 con vision client que retorna factura completa."""
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=GoodVisionClient(),
    )


@pytest.fixture()
def orchestrator_missing(redis_client):
    """OrchestratorV2 con vision client que omite el monto crítico."""
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=MissingCriticalVisionClient(),
    )


@pytest.fixture()
def orchestrator_failing(redis_client):
    """OrchestratorV2 cuyo vision client lanza RuntimeError."""
    return OrchestratorV2(
        redis_client=redis_client,
        vision_client=FailingVisionClient(),
    )


@pytest.fixture()
def flow():
    """FlowCoordinator — pipeline completo sin Redis."""
    return FlowCoordinator()


@pytest.fixture()
def centinela():
    """CentinelaGuardian limpio."""
    return CentinelaGuardian()


@pytest.fixture()
def aprendizaje():
    """AprendizajeFederado limpio."""
    return AprendizajeFederado()


@pytest.fixture()
def api_client():
    """FastAPI TestClient con app completa."""
    return TestClient(app)


# ── Auth header helpers ──────────────────────────────────────────────────────

@pytest.fixture()
def client_headers():
    token = create_test_token(role=Role.CLIENT, client_id="client-demo-001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client_b_headers():
    """Cliente distinto — para tests de scope."""
    token = create_test_token(role=Role.CLIENT, client_id="client-demo-002")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def cpa_headers():
    token = create_test_token(role=Role.CPA_PARTNER, client_id="client-demo-001")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers():
    token = create_test_token(role=Role.EXIMIA_ADMIN, client_id=None)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def no_mfa_headers():
    """Token con mfa_pending=True — MFA no completado."""
    from api.auth import create_access_token
    token = create_access_token({
        "sub": "user-nomfa",
        "username": "nomfa_user",
        "role": "CPA_PARTNER",
        "client_id": "client-demo-001",
        "mfa_verified": False,
        "mfa_pending": True,
    })
    return {"Authorization": f"Bearer {token}"}
