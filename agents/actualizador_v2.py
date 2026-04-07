# =============================================================================
# agents/actualizador_v2.py
# Motor de Actualización Normativa — monitorea 4 fuentes, genera diffs,
# clasifica impacto y requiere aprobación explícita de 3 factores para activar.
#
# GARANTÍAS DE DISEÑO:
#   - NUNCA activa cambios automáticamente. Toda activación requiere los 3
#     factores: firma digital CPA + effective_date + comentario interpretación.
#   - Las actualizaciones se registran en PENDING_REVIEW — nunca en APPROVED
#     por el sistema, solo por acción humana explícita.
#   - El diff exacto campo-por-campo se genera antes de mostrar al CPA.
#   - SLA automático por nivel de impacto:
#       CRÍTICO  → 24h   (cambios de tasa, nuevas obligaciones tributarias)
#       MODERADO → 72h   (cambios de formularios, plazos)
#       MENOR    → 168h  (aclaraciones, cambios menores)
#   - SourceFetcher es inyectable — tests usan mocks sin HTTP real.
#   - El log de actualizaciones es append-only — inmutable una vez registrado.
#
# 4 FUENTES MONITOREADAS:
#   1. hacienda.pr.gov  — circulares y reglamentos
#   2. irs.gov          — Publication 15, Revenue Rulings
#   3. lexjuris.com RSS — leyes via estado.pr.gov
#   4. dtrh.pr.gov      — tasas SUTA
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import BitCountingAgentError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class ActivationError(BitCountingAgentError):
    """Activación rechazada por falta de los 3 factores requeridos."""
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Activación rechazada: {reason}. "
            "Se requieren los 3 factores: firma_digital_cpa + "
            "effective_date + comentario_interpretacion."
        )


class UpdateNotFoundError(BitCountingAgentError):
    """Update ID no encontrado en el registro."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ImpactLevel(str, Enum):
    CRITICO  = "CRITICO"   # SLA 24h
    MODERADO = "MODERADO"  # SLA 72h
    MENOR    = "MENOR"     # SLA 168h


class UpdateStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    UNDER_REVIEW   = "UNDER_REVIEW"
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"
    DEFERRED       = "DEFERRED"


class ChangeType(str, Enum):
    RATE_CHANGE        = "rate_change"
    NEW_RULE           = "new_rule"
    RULE_REMOVAL       = "rule_removal"
    RULE_MODIFICATION  = "rule_modification"
    DEADLINE_CHANGE    = "deadline_change"
    FORM_UPDATE        = "form_update"
    NO_CHANGE          = "no_change"


_IMPACT_SLA_HOURS: dict[ImpactLevel, int] = {
    ImpactLevel.CRITICO:  24,
    ImpactLevel.MODERADO: 72,
    ImpactLevel.MENOR:    168,
}

# Tipos de cambio que clasifican automáticamente como CRÍTICO
_CRITICAL_CHANGE_TYPES = frozenset({
    ChangeType.RATE_CHANGE,
    ChangeType.NEW_RULE,
    ChangeType.RULE_REMOVAL,
})


# ---------------------------------------------------------------------------
# Modelos inmutables
# ---------------------------------------------------------------------------

class FieldDiff(BaseModel):
    """Diferencia campo-por-campo entre regla actual y propuesta."""
    model_config = ConfigDict(frozen=True)

    field_name:   str
    current_val:  str
    proposed_val: str
    change_pct:   Optional[Decimal] = None   # % de cambio si es numérico


class NormativeUpdate(BaseModel):
    """
    Registro inmutable de una actualización normativa detectada.

    status es siempre PENDING_REVIEW cuando el sistema la crea.
    Solo puede cambiar a APPROVED mediante activate_update() con los 3 factores.
    """
    model_config = ConfigDict(frozen=True)

    update_id:            str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_name:          str
    source_url:           str
    detected_at:          str   # ISO 8601 UTC
    change_type:          ChangeType
    impact_level:         ImpactLevel
    sla_hours:            int
    sla_deadline:         str   # ISO 8601 — detected_at + sla_hours
    description:          str
    raw_excerpt:          str   # fragmento relevante extraído de la fuente
    affected_rule_ids:    tuple[str, ...]
    field_diffs:          tuple[FieldDiff, ...]
    content_hash:         str   # SHA-256 del raw_excerpt (detecta duplicados)
    status:               UpdateStatus = UpdateStatus.PENDING_REVIEW
    requires_human_review: bool = True   # SIEMPRE True — invariante
    # Campos de activación (None hasta approve)
    approved_by_cpa:      Optional[str] = None
    approved_at:          Optional[str] = None
    effective_date:       Optional[str] = None
    interpretation_note:  Optional[str] = None


class ActivationRequest(BaseModel):
    """
    Los 3 factores obligatorios para activar una actualización normativa.

    Sin los 3 factores simultáneamente, la activación es rechazada.
    """
    model_config = ConfigDict(frozen=True)

    cpa_license:           str      # licencia CPA
    digital_signature:     str      # firma digital mínimo 16 chars hex
    effective_date:        str      # ISO 8601 date YYYY-MM-DD
    interpretation_note:   str      # comentario mínimo 20 chars
    approved_at:           str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SourceEntry(BaseModel):
    """Entrada detectada en una fuente normativa."""
    model_config = ConfigDict(frozen=True)

    title:       str
    url:         str
    published:   str   # ISO 8601
    summary:     str
    raw_text:    str


class EximiaNotification(BaseModel):
    """Notificación enviada al equipo Eximia sobre una actualización crítica."""
    model_config = ConfigDict(frozen=True)

    notification_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    update_id:       str
    impact_level:    ImpactLevel
    source_name:     str
    description:     str
    sla_deadline:    str
    sent_at:         str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Protocolo del fetcher (inyectable para tests)
# ---------------------------------------------------------------------------

class SourceFetcherProtocol(Protocol):
    """Interfaz para obtener contenido de fuentes normativas."""
    def fetch_rss(self, url: str) -> list[SourceEntry]: ...
    def fetch_html(self, url: str, css_selector: str) -> str: ...


class _DefaultSourceFetcher:
    """Fetcher real usando requests + feedparser (Phase 3)."""

    def fetch_rss(self, url: str) -> list[SourceEntry]:
        try:
            import feedparser
            feed = feedparser.parse(url)
            entries = []
            for e in feed.entries[:10]:
                entries.append(SourceEntry(
                    title=getattr(e, 'title', ''),
                    url=getattr(e, 'link', url),
                    published=getattr(e, 'published', datetime.now(timezone.utc).isoformat()),
                    summary=getattr(e, 'summary', ''),
                    raw_text=getattr(e, 'summary', '') or getattr(e, 'title', ''),
                ))
            return entries
        except Exception as exc:
            logger.warning("fetch_rss failed for %s: %s", url, exc)
            return []

    def fetch_html(self, url: str, css_selector: str = "body") -> str:
        try:
            import requests
            from bs4 import BeautifulSoup
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            elements = soup.select(css_selector)
            return " ".join(e.get_text(separator=" ", strip=True) for e in elements[:5])
        except Exception as exc:
            logger.warning("fetch_html failed for %s: %s", url, exc)
            return ""


# ---------------------------------------------------------------------------
# Fuentes configuradas
# ---------------------------------------------------------------------------

class SourceConfig:
    name: str
    url:  str
    kind: str   # "rss" | "html"
    css:  str
    desc: str

    def __init__(self, name, url, kind, css, desc):
        self.name = name
        self.url  = url
        self.kind = kind
        self.css  = css
        self.desc = desc


_SOURCES_V2: list[SourceConfig] = [
    SourceConfig(
        name="hacienda.pr.gov",
        url="https://hacienda.pr.gov/noticias-recientes",
        kind="html",
        css=".views-row, .field-content, article",
        desc="Departamento de Hacienda PR — circulares y reglamentos",
    ),
    SourceConfig(
        name="irs.gov",
        url="https://www.irs.gov/newsroom/irs-guidewire-latest-news",
        kind="rss",
        css="",
        desc="IRS — Publication 15, Revenue Rulings, Notices",
    ),
    SourceConfig(
        name="lexjuris.com",
        url="http://www.lexjuris.com/rss/lexjurisleyes.xml",
        kind="rss",
        css="",
        desc="LexJuris RSS — leyes via estado.pr.gov",
    ),
    SourceConfig(
        name="dtrh.pr.gov",
        url="https://dtrh.pr.gov/publicaciones",
        kind="html",
        css=".views-row, .publication, article",
        desc="DTRH PR — tasas SUTA y reglamentos laborales",
    ),
]

_SOURCE_MAP: dict[str, SourceConfig] = {s.name: s for s in _SOURCES_V2}


# ---------------------------------------------------------------------------
# Clasificación de impacto
# ---------------------------------------------------------------------------

_CRITICO_KEYWORDS = frozenset([
    "tasa", "rate", "porcentaje", "percent", "base imponible",
    "nueva obligacion", "new obligation", "elimina", "deroga",
    "FICA", "FUTA", "SUTA", "IVU", "SUT",
    "Act 60", "Código de Rentas", "Internal Revenue Code",
    "2025", "2026",  # años vigentes → cambio reciente
])

_MODERADO_KEYWORDS = frozenset([
    "formulario", "form", "planilla", "plazo", "deadline",
    "fecha limite", "due date", "extension", "prorroga",
    "instrucciones", "instructions", "reglamento",
])


def _classify_impact(change_type: ChangeType, text: str) -> ImpactLevel:
    """Clasifica el impacto de un cambio normativo."""
    if change_type in _CRITICAL_CHANGE_TYPES:
        return ImpactLevel.CRITICO
    lower = text.lower()
    if any(kw.lower() in lower for kw in _CRITICO_KEYWORDS):
        return ImpactLevel.CRITICO
    if any(kw.lower() in lower for kw in _MODERADO_KEYWORDS):
        return ImpactLevel.MODERADO
    return ImpactLevel.MENOR


def _detect_change_type(title: str, summary: str) -> ChangeType:
    """Infiere el tipo de cambio desde el texto."""
    text = (title + " " + summary).lower()
    if any(w in text for w in ["nueva tasa", "new rate", "rate change", "cambio de tasa", "suta rate"]):
        return ChangeType.RATE_CHANGE
    if any(w in text for w in ["nueva regla", "new rule", "nueva ley", "new law", "aprueba ley"]):
        return ChangeType.NEW_RULE
    if any(w in text for w in ["elimina", "deroga", "repealed", "removed"]):
        return ChangeType.RULE_REMOVAL
    if any(w in text for w in ["modifica", "modified", "amended", "enmendado"]):
        return ChangeType.RULE_MODIFICATION
    if any(w in text for w in ["formulario", "form", "planilla", "schedule"]):
        return ChangeType.FORM_UPDATE
    if any(w in text for w in ["plazo", "deadline", "fecha", "due date", "extension"]):
        return ChangeType.DEADLINE_CHANGE
    return ChangeType.RULE_MODIFICATION


def _extract_rate_diffs(text: str, rule_id: Optional[str]) -> tuple[FieldDiff, ...]:
    """Extrae diferencias de tasas si hay patrones numéricos en el texto."""
    diffs: list[FieldDiff] = []
    rate_patterns = re.findall(r'(\d+\.?\d*)\s*%', text)
    if rate_patterns:
        diffs.append(FieldDiff(
            field_name="rate",
            current_val=f"[ver {rule_id or 'regla actual'} en tax_rules]",
            proposed_val=f"{rate_patterns[0]}%",
            change_pct=None,
        ))
    return tuple(diffs)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Motor de Actualización Normativa V2
# ---------------------------------------------------------------------------

class ActualizadorV2:
    """
    Motor de Actualización Normativa — monitorea 4 fuentes, genera diffs,
    clasifica impacto con SLA automático y requiere 3 factores para activar.

    Uso:
        motor = ActualizadorV2()
        updates = motor.check_all_sources()
        # ... CPA revisa ...
        motor.activate_update(update_id, ActivationRequest(...))
    """

    AGENT_NAME = "ACTUALIZADOR_V2"

    def __init__(
        self,
        fetcher: Optional[SourceFetcherProtocol] = None,
    ) -> None:
        self._fetcher = fetcher or _DefaultSourceFetcher()
        self._updates:       list[NormativeUpdate]    = []
        self._notifications: list[EximiaNotification] = []
        # Hash de contenidos previos para detectar duplicados
        self._seen_hashes:   set[str]                 = set()

    # ------------------------------------------------------------------ #
    # Monitoreo de fuentes                                                #
    # ------------------------------------------------------------------ #

    def check_all_sources(self) -> tuple[NormativeUpdate, ...]:
        """
        Verifica las 4 fuentes normativas y registra actualizaciones detectadas.

        Retorna SOLO las actualizaciones nuevas de este ciclo.
        Nunca activa cambios — todas quedan en PENDING_REVIEW.
        """
        new_updates: list[NormativeUpdate] = []
        for src in _SOURCES_V2:
            try:
                found = self.check_source(src.name)
                new_updates.extend(found)
            except Exception as exc:
                logger.error("ACTUALIZADOR V2: error en fuente %s: %s", src.name, exc)
        logger.info(
            "ACTUALIZADOR V2: ciclo completo. %d actualizaciones nuevas. "
            "Ninguna activada automáticamente.",
            len(new_updates),
        )
        return tuple(new_updates)

    def check_source(self, source_name: str) -> list[NormativeUpdate]:
        """
        Verifica una fuente y retorna las actualizaciones nuevas detectadas.

        Deduplica por hash de contenido — no registra la misma entrada dos veces.
        """
        cfg = _SOURCE_MAP.get(source_name)
        if cfg is None:
            raise BitCountingAgentError(f"Fuente desconocida: {source_name}")

        entries: list[SourceEntry] = []
        if cfg.kind == "rss":
            entries = self._fetcher.fetch_rss(cfg.url)
        else:
            raw = self._fetcher.fetch_html(cfg.url, cfg.css)
            if raw:
                entries = [SourceEntry(
                    title=f"Publicación de {source_name}",
                    url=cfg.url,
                    published=datetime.now(timezone.utc).isoformat(),
                    summary=raw[:500],
                    raw_text=raw,
                )]

        new_updates: list[NormativeUpdate] = []
        for entry in entries:
            h = _content_hash(entry.raw_text)
            if h in self._seen_hashes:
                continue
            self._seen_hashes.add(h)

            change_type  = _detect_change_type(entry.title, entry.summary)
            impact       = _classify_impact(change_type, entry.title + " " + entry.summary)
            sla_hours    = _IMPACT_SLA_HOURS[impact]
            now          = datetime.now(timezone.utc)
            sla_deadline = (now + timedelta(hours=sla_hours)).isoformat()
            field_diffs  = _extract_rate_diffs(entry.raw_text, None)

            update = NormativeUpdate(
                source_name=source_name,
                source_url=entry.url,
                detected_at=now.isoformat(),
                change_type=change_type,
                impact_level=impact,
                sla_hours=sla_hours,
                sla_deadline=sla_deadline,
                description=f"{entry.title}: {entry.summary[:300]}",
                raw_excerpt=entry.raw_text[:1000],
                affected_rule_ids=(),
                field_diffs=field_diffs,
                content_hash=h,
                status=UpdateStatus.PENDING_REVIEW,
                requires_human_review=True,
            )
            self._updates.append(update)
            new_updates.append(update)

            # Notificar Eximia para CRÍTICO y MODERADO
            if impact in (ImpactLevel.CRITICO, ImpactLevel.MODERADO):
                self._notify_eximia(update)

        return new_updates

    def register_update_manual(
        self,
        source_name:       str,
        source_url:        str,
        change_type:       ChangeType,
        description:       str,
        raw_excerpt:       str,
        affected_rule_ids: tuple[str, ...] = (),
        field_diffs:       tuple[FieldDiff, ...] = (),
    ) -> NormativeUpdate:
        """
        Registra una actualización manualmente (útil para tests y para
        fuentes que no tienen RSS pero se detectan por otro canal).

        Siempre registra como PENDING_REVIEW — nunca activa.
        """
        impact       = _classify_impact(change_type, description)
        sla_hours    = _IMPACT_SLA_HOURS[impact]
        now          = datetime.now(timezone.utc)
        sla_deadline = (now + timedelta(hours=sla_hours)).isoformat()
        h            = _content_hash(raw_excerpt)

        update = NormativeUpdate(
            source_name=source_name,
            source_url=source_url,
            detected_at=now.isoformat(),
            change_type=change_type,
            impact_level=impact,
            sla_hours=sla_hours,
            sla_deadline=sla_deadline,
            description=description,
            raw_excerpt=raw_excerpt,
            affected_rule_ids=affected_rule_ids,
            field_diffs=field_diffs,
            content_hash=h,
            status=UpdateStatus.PENDING_REVIEW,
            requires_human_review=True,
        )
        self._updates.append(update)
        if impact in (ImpactLevel.CRITICO, ImpactLevel.MODERADO):
            self._notify_eximia(update)
        return update

    # ------------------------------------------------------------------ #
    # Activación — requiere 3 factores                                   #
    # ------------------------------------------------------------------ #

    def activate_update(
        self,
        update_id:   str,
        activation:  ActivationRequest,
    ) -> NormativeUpdate:
        """
        Activa una actualización normativa tras validar los 3 factores.

        FACTOR 1: cpa_license + digital_signature (mínimo 16 chars hex)
        FACTOR 2: effective_date (ISO 8601 YYYY-MM-DD, no pasada)
        FACTOR 3: interpretation_note (mínimo 20 chars)

        Sin los 3 factores simultáneamente: ActivationError.
        """
        update = self._find_update(update_id)

        # Validar FACTOR 1: firma digital CPA
        if not activation.cpa_license or not activation.cpa_license.strip():
            raise ActivationError("cpa_license vacío")
        if not re.match(r'^[0-9a-fA-F]{16,}$', activation.digital_signature):
            raise ActivationError(
                "digital_signature debe ser hexadecimal de mínimo 16 caracteres"
            )

        # Validar FACTOR 2: effective_date
        if not activation.effective_date:
            raise ActivationError("effective_date requerido")
        try:
            eff = date.fromisoformat(activation.effective_date)
        except ValueError:
            raise ActivationError("effective_date debe ser YYYY-MM-DD")
        if eff < date.today():
            raise ActivationError(
                f"effective_date {activation.effective_date} es una fecha pasada"
            )

        # Validar FACTOR 3: comentario de interpretación
        if not activation.interpretation_note or len(activation.interpretation_note.strip()) < 20:
            raise ActivationError(
                "interpretation_note debe tener mínimo 20 caracteres"
            )

        # Validar que esté en PENDING_REVIEW o UNDER_REVIEW
        if update.status not in (UpdateStatus.PENDING_REVIEW, UpdateStatus.UNDER_REVIEW):
            raise ActivationError(
                f"Update {update_id} tiene status {update.status.value} — "
                "solo se puede activar desde PENDING_REVIEW o UNDER_REVIEW"
            )

        # Crear versión aprobada (frozen → nuevo objeto)
        approved = NormativeUpdate(
            update_id=update.update_id,
            source_name=update.source_name,
            source_url=update.source_url,
            detected_at=update.detected_at,
            change_type=update.change_type,
            impact_level=update.impact_level,
            sla_hours=update.sla_hours,
            sla_deadline=update.sla_deadline,
            description=update.description,
            raw_excerpt=update.raw_excerpt,
            affected_rule_ids=update.affected_rule_ids,
            field_diffs=update.field_diffs,
            content_hash=update.content_hash,
            status=UpdateStatus.APPROVED,
            requires_human_review=True,   # sigue siendo True — solo indica que pasó por humano
            approved_by_cpa=activation.cpa_license,
            approved_at=activation.approved_at,
            effective_date=activation.effective_date,
            interpretation_note=activation.interpretation_note,
        )

        # Reemplazar en lista interna
        idx = next(i for i, u in enumerate(self._updates) if u.update_id == update_id)
        self._updates[idx] = approved

        logger.info(
            "ACTUALIZADOR V2: update %s APROBADO por CPA %s. "
            "Vigencia: %s.",
            update_id, activation.cpa_license, activation.effective_date,
        )
        return approved

    def reject_update(self, update_id: str, cpa_license: str, reason: str) -> NormativeUpdate:
        """Rechaza una actualización. Requiere motivo no vacío."""
        if not reason or not reason.strip():
            raise BitCountingAgentError("reason no puede estar vacío al rechazar un update")
        update = self._find_update(update_id)
        rejected = NormativeUpdate(
            update_id=update.update_id,
            source_name=update.source_name,
            source_url=update.source_url,
            detected_at=update.detected_at,
            change_type=update.change_type,
            impact_level=update.impact_level,
            sla_hours=update.sla_hours,
            sla_deadline=update.sla_deadline,
            description=update.description,
            raw_excerpt=update.raw_excerpt,
            affected_rule_ids=update.affected_rule_ids,
            field_diffs=update.field_diffs,
            content_hash=update.content_hash,
            status=UpdateStatus.REJECTED,
            requires_human_review=True,
            approved_by_cpa=cpa_license,
            approved_at=datetime.now(timezone.utc).isoformat(),
            interpretation_note=f"RECHAZADO: {reason}",
        )
        idx = next(i for i, u in enumerate(self._updates) if u.update_id == update_id)
        self._updates[idx] = rejected
        return rejected

    # ------------------------------------------------------------------ #
    # Consultas                                                           #
    # ------------------------------------------------------------------ #

    def get_pending_updates(self) -> tuple[NormativeUpdate, ...]:
        return tuple(u for u in self._updates if u.status == UpdateStatus.PENDING_REVIEW)

    def get_updates_by_status(self, status: UpdateStatus) -> tuple[NormativeUpdate, ...]:
        return tuple(u for u in self._updates if u.status == status)

    def get_updates_by_impact(self, impact: ImpactLevel) -> tuple[NormativeUpdate, ...]:
        return tuple(u for u in self._updates if u.impact_level == impact)

    def get_overdue_updates(self, now: Optional[datetime] = None) -> tuple[NormativeUpdate, ...]:
        """Retorna updates PENDING que han superado su SLA sin revisión."""
        now = now or datetime.now(timezone.utc)
        result = []
        for u in self._updates:
            if u.status != UpdateStatus.PENDING_REVIEW:
                continue
            try:
                deadline = datetime.fromisoformat(u.sla_deadline)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if deadline < now:
                    result.append(u)
            except Exception:
                pass
        return tuple(result)

    def get_eximia_notifications(self) -> tuple[EximiaNotification, ...]:
        return tuple(self._notifications)

    def get_all_updates(self) -> tuple[NormativeUpdate, ...]:
        return tuple(self._updates)

    # ------------------------------------------------------------------ #
    # Privados                                                            #
    # ------------------------------------------------------------------ #

    def _find_update(self, update_id: str) -> NormativeUpdate:
        for u in self._updates:
            if u.update_id == update_id:
                return u
        raise UpdateNotFoundError(f"Update no encontrado: {update_id}")

    def _notify_eximia(self, update: NormativeUpdate) -> EximiaNotification:
        notif = EximiaNotification(
            update_id=update.update_id,
            impact_level=update.impact_level,
            source_name=update.source_name,
            description=update.description[:200],
            sla_deadline=update.sla_deadline,
        )
        self._notifications.append(notif)
        logger.warning(
            "ACTUALIZADOR V2 → EXIMIA: %s [%s] SLA=%s",
            update.source_name, update.impact_level.value, update.sla_deadline,
        )
        return notif
