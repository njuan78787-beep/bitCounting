# =============================================================================
# api/db/seed.py
# Seed demo data for Bit-Counting development / demo environments.
#
# Called during application startup (lifespan) when the database is empty.
# Safe to run multiple times — uses "insert if not exists" pattern.
#
# DEMO USERS:
#   admin@eximia.pr      / Admin2026!Secure    → EXIMIA_ADMIN
#   cpa_senior@eximia.pr / CPA2026!Senior      → CPA_SENIOR
#   cpa_partner@eximia.pr/ CPA2026!Partner     → CPA_PARTNER
#   cliente@empresa.pr   / Client2026!Demo     → CLIENT
#
# MFA: Any 6-digit code is accepted in demo mode (totp_secret set to DEMO_SECRET).
# =============================================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AppCentinaelaPause,
    AppNormativeUpdate,
    AppReviewQueueItem,
    AppTransaction,
    AppUser,
)
from ..auth import Role, _hash_password, generate_totp_secret

logger = logging.getLogger(__name__)

# DEMO TOTP secret — known value so tests can generate valid codes
DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXP"   # base32 for "Hello!"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_DEMO_USERS = [
    {
        "username": "admin@eximia.pr",
        "password": "Admin2026!Secure",
        "role": Role.EXIMIA_ADMIN.value,
        "client_id": None,
    },
    {
        "username": "cpa_senior@eximia.pr",
        "password": "CPA2026!Senior",
        "role": Role.CPA_SENIOR.value,
        "client_id": None,
    },
    {
        "username": "cpa_partner@eximia.pr",
        "password": "CPA2026!Partner",
        "role": Role.CPA_PARTNER.value,
        "client_id": "client-demo-001",
    },
    {
        "username": "cliente@empresa.pr",
        "password": "Client2026!Demo",
        "role": Role.CLIENT.value,
        "client_id": "client-demo-001",
    },
    # Demo login (matches frontend mock — any password works because
    # mock intercepts; but real backend needs a real password)
    {
        "username": "demo",
        "password": "demo",
        "role": Role.CLIENT.value,
        "client_id": "client-demo-001",
    },
    {
        "username": "cpa.demo",
        "password": "demo",
        "role": Role.CPA_PARTNER.value,
        "client_id": None,
    },
]


async def seed_users(db: AsyncSession) -> None:
    """Insert demo users if they don't already exist."""
    for u in _DEMO_USERS:
        result = await db.execute(
            select(AppUser).where(AppUser.username == u["username"])
        )
        if result.scalar_one_or_none() is not None:
            continue   # Already exists

        db.add(AppUser(
            id=str(uuid.uuid4()),
            username=u["username"],
            password_hash=_hash_password(u["password"]),
            role=u["role"],
            client_id=u["client_id"],
            totp_secret=DEMO_TOTP_SECRET,  # shared secret for demo
            mfa_enabled=True,
            is_active=True,
        ))

    await db.commit()
    logger.info("Demo users seeded (or already exist)")


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

_DEMO_TRANSACTIONS = [
    {
        "transaction_id": "txn-demo-001",
        "client_id": "client-demo-001",
        "vendor": "Costco Wholesale PR",
        "amount": Decimal("2450.00"),
        "date": "2026-03-15",
        "status": "processed",
        "account_code": "5900",
        "account_name": "Gastos de Operacion General",
        "confidence": Decimal("0.91"),
    },
    {
        "transaction_id": "txn-demo-002",
        "client_id": "client-demo-001",
        "vendor": "Dell Technologies",
        "amount": Decimal("3200.00"),
        "date": "2026-03-20",
        "status": "processed",
        "account_code": "1500",
        "account_name": "Equipos de Computacion",
        "confidence": Decimal("0.88"),
    },
    {
        "transaction_id": "txn-demo-003",
        "client_id": "client-demo-001",
        "vendor": "Hacienda PR",
        "amount": Decimal("1250.00"),
        "date": "2026-03-31",
        "status": "paused",
        "account_code": "2400",
        "account_name": "Impuestos por Pagar",
        "confidence": Decimal("0.55"),
    },
]


async def seed_transactions(db: AsyncSession) -> None:
    """Insert demo transactions if they don't already exist."""
    for t in _DEMO_TRANSACTIONS:
        result = await db.execute(
            select(AppTransaction).where(
                AppTransaction.transaction_id == t["transaction_id"]
            )
        )
        if result.scalar_one_or_none() is not None:
            continue

        db.add(AppTransaction(
            transaction_id=t["transaction_id"],
            client_id=t["client_id"],
            vendor=t["vendor"],
            amount=t["amount"],
            date=t["date"],
            status=t["status"],
            account_code=t["account_code"],
            account_name=t["account_name"],
            confidence=t["confidence"],
        ))

    await db.commit()
    logger.info("Demo transactions seeded (or already exist)")


# ---------------------------------------------------------------------------
# CENTINELA pauses
# ---------------------------------------------------------------------------

async def seed_pauses(db: AsyncSession) -> None:
    """Insert demo CENTINELA pauses if they don't already exist."""
    demos = [
        {
            "pause_id": "pause-demo-001",
            "client_id": "client-demo-001",
            "trigger_type": "LOW_CONFIDENCE",
            "affected_transaction_ids": ["txn-demo-003"],
            "conflicting_rules": [],
            "interpretations": [
                "Clasificar como pago estimado de ingresos (cuenta 2400)",
                "Clasificar como pago de IVU mensual (cuenta 2410)",
            ],
            "pre_processed_analysis": (
                "Transaccion de $1,250 pagada a Hacienda PR el 2026-03-31. "
                "El confidence del INTAKE fue 0.55 (umbral: 0.60) debido a "
                "descripcion ambigua. Puede ser pago de IVU mensual o estimado "
                "de ingresos — el CPA debe determinar la clasificacion correcta."
            ),
            "sla_hours": 48,
            "sla_deadline": datetime.utcnow() + timedelta(hours=48),
            "status": "ACTIVE",
            "assigned_cpa_license": None,
        },
        {
            "pause_id": "pause-demo-002",
            "client_id": "client-demo-001",
            "trigger_type": "CONFLICTING_RULES",
            "affected_transaction_ids": ["txn-demo-002"],
            "conflicting_rules": ["PR-CAP-THRESHOLD-2500", "GAAP-EXPENSE-RECOGNITION"],
            "interpretations": [
                "Capitalizar como activo fijo — Equipos de Computacion (1500)",
                "Registrar como gasto inmediato — Gastos de Operacion (5900)",
            ],
            "pre_processed_analysis": (
                "Compra de $3,200 a Dell Technologies. El monto supera el umbral "
                "de capitalizacion de $2,500 (regla PR-CAP-THRESHOLD-2500), pero "
                "la factura no especifica depreciacion. Se requiere criterio del CPA."
            ),
            "sla_hours": 24,
            "sla_deadline": datetime.utcnow() + timedelta(hours=24),
            "status": "ACTIVE",
            "assigned_cpa_license": None,
        },
    ]

    for p in demos:
        result = await db.execute(
            select(AppCentinaelaPause).where(
                AppCentinaelaPause.pause_id == p["pause_id"]
            )
        )
        if result.scalar_one_or_none() is not None:
            continue

        db.add(AppCentinaelaPause(
            pause_id=p["pause_id"],
            client_id=p["client_id"],
            trigger_type=p["trigger_type"],
            affected_transaction_ids=p["affected_transaction_ids"],
            conflicting_rules=p["conflicting_rules"],
            interpretations=p["interpretations"],
            pre_processed_analysis=p["pre_processed_analysis"],
            sla_hours=p["sla_hours"],
            sla_deadline=p["sla_deadline"],
            status=p["status"],
            assigned_cpa_license=p["assigned_cpa_license"],
        ))

    await db.commit()
    logger.info("Demo CENTINELA pauses seeded (or already exist)")


# ---------------------------------------------------------------------------
# Review queue items
# ---------------------------------------------------------------------------

async def seed_review_queue(db: AsyncSession) -> None:
    """Insert demo review queue items if they don't already exist."""
    demos = [
        {
            "item_id": "review-demo-001",
            "item_type": "journal_entry",
            "transaction_id": "txn-demo-002",
            "client_id": "client-demo-001",
            "vendor": "Dell Technologies",
            "amount": Decimal("3200.00"),
            "description": "Computer equipment purchase — capital vs expense determination",
            "consequence_level": "HIGH",
            "sla_deadline": datetime.utcnow() + timedelta(hours=24),
            "status": "pending_review",
            "detail": {
                "account_code": "1500",
                "account_name": "Equipos de Computacion",
                "rule_ref": "PR-CAP-THRESHOLD-2500",
                "reasoning": "Amount $3,200 exceeds $2,500 capitalization threshold. Classified as capital asset.",
            },
        },
        {
            "item_id": "review-demo-002",
            "item_type": "expense_entry",
            "transaction_id": "txn-demo-001",
            "client_id": "client-demo-001",
            "vendor": "Costco Wholesale PR",
            "amount": Decimal("2450.00"),
            "description": "Supplies purchase below $2,500 threshold",
            "consequence_level": "LOW",
            "sla_deadline": datetime.utcnow() + timedelta(hours=72),
            "status": "pending_review",
            "detail": {
                "account_code": "5900",
                "account_name": "Gastos de Operacion General",
                "rule_ref": "GAAP-EXPENSE-RECOGNITION",
                "reasoning": "Amount $2,450 is below $2,500 threshold; expensed directly.",
            },
        },
    ]

    for item in demos:
        result = await db.execute(
            select(AppReviewQueueItem).where(
                AppReviewQueueItem.item_id == item["item_id"]
            )
        )
        if result.scalar_one_or_none() is not None:
            continue

        db.add(AppReviewQueueItem(**item))

    await db.commit()
    logger.info("Demo review queue items seeded (or already exist)")


# ---------------------------------------------------------------------------
# Normative updates
# ---------------------------------------------------------------------------

async def seed_normative_updates(db: AsyncSession) -> None:
    """Insert demo normative updates if they don't already exist."""
    demos = [
        {
            "update_id": "norm-demo-001",
            "source": "Hacienda PR",
            "title": "Actualización Tasa IVU Municipal — Determinación 2026-03",
            "description": (
                "Hacienda PR emitió la Determinación Administrativa 2026-03 modificando "
                "la base imponible para servicios digitales. Aplica a partir del 1ro de mayo 2026."
            ),
            "effective_date": "2026-05-01",
            "detected_at": datetime(2026, 4, 1, 8, 0, 0),
            "status": "pending",
            "affected_rules": ["IVU-PR-2021-001", "IVU-PR-DIGITAL-001"],
            "approved_by_cpa": None,
            "approved_at": None,
        },
        {
            "update_id": "norm-demo-002",
            "source": "Junta de Supervisión Fiscal",
            "title": "Plan Fiscal 2026 — Revisión de Incentivos Industriales",
            "description": (
                "La Junta de Supervisión Fiscal publicó revisiones al Plan Fiscal 2026 "
                "que afectan los incentivos del Acta 60. Período de comentarios abierto."
            ),
            "effective_date": "2026-07-01",
            "detected_at": datetime(2026, 3, 28, 14, 0, 0),
            "status": "pending",
            "affected_rules": ["PR-ACT60-INCENTIVE-001"],
            "approved_by_cpa": None,
            "approved_at": None,
        },
        {
            "update_id": "norm-demo-003",
            "source": "CRIM",
            "title": "Tasas de Contribución sobre la Propiedad 2026-2027",
            "description": (
                "CRIM publicó las tasas actualizadas de contribución sobre la propiedad "
                "para el año fiscal 2026-2027."
            ),
            "effective_date": "2026-07-01",
            "detected_at": datetime(2026, 4, 2, 10, 0, 0),
            "status": "approved",
            "affected_rules": ["CRIM-PROPERTY-TAX-001"],
            "approved_by_cpa": "CPA-PR-12345",
            "approved_at": datetime(2026, 4, 3, 9, 0, 0),
        },
    ]

    for u in demos:
        result = await db.execute(
            select(AppNormativeUpdate).where(
                AppNormativeUpdate.update_id == u["update_id"]
            )
        )
        if result.scalar_one_or_none() is not None:
            continue

        db.add(AppNormativeUpdate(**u))

    await db.commit()
    logger.info("Demo normative updates seeded (or already exist)")


# ---------------------------------------------------------------------------
# Master seed entry point
# ---------------------------------------------------------------------------

async def run_seed(db: AsyncSession) -> None:
    """Run all seed functions in dependency order."""
    await seed_users(db)
    await seed_transactions(db)
    await seed_pauses(db)
    await seed_review_queue(db)
    await seed_normative_updates(db)
    logger.info("All demo seed data loaded successfully")
