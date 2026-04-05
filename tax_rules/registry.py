# =============================================================================
# tax_rules/registry.py
# Registro inmutable de reglas fiscales de Puerto Rico.
#
# REGLA DE ORO:
#   - Este modulo es de SOLO LECTURA para todos los agentes del sistema.
#   - _RULES_REGISTRY no puede ser modificado en runtime por ningun agente.
#   - Solo admin.py con autenticacion separada puede agregar/actualizar reglas.
#   - El LLM nunca escribe en este modulo — arquitectonicamente imposible.
# =============================================================================

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from .models import TaxRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# REGLAS INICIALES — Puerto Rico 2025
# Cada regla tiene todos sus campos obligatorios.
# Las reglas expiran cuando son supersedidas — la regla nueva referencia
# a la anterior via supersedes_rule_id.
# ---------------------------------------------------------------------------

_INITIAL_RULES: list[TaxRule] = [

    # -------------------------------------------------------------------------
    # IVU ESTATAL — 10.5%
    # Ley Num. 72-2015 que enmendo el CRIR (Ley 1-2011), Sec. 4010.01
    # Vigente desde 1 de octubre de 2015
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="IVU_ESTATAL_PR_2015_V1",
        name="IVU Estatal Puerto Rico — 10.5%",
        description=(
            "Impuesto sobre Ventas y Uso estatal de Puerto Rico a una tasa del 10.5%. "
            "Aplica a la venta, uso, consumo e introduccion de articulos de propiedad "
            "personal tangible y servicios designados. Existen exenciones especificas "
            "para articulos de primera necesidad y actividades productivas."
        ),
        legal_reference=(
            "Codigo de Rentas Internas de Puerto Rico (CRIR), Ley 1-2011, "
            "Seccion 4010.01, enmendada por Ley Num. 72 del 29 de mayo de 2015. "
            "Tasa estatal incrementada de 7.0% a 10.5%, efectivo 1 de octubre de 2015."
        ),
        source_url="https://www.hacienda.pr.gov/sites/default/files/ley1-2011.pdf",
        published_date=date(2015, 5, 29),
        effective_date=date(2015, 10, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="IVU_ESTATAL",
        jurisdiction="PR",
        rate=Decimal("10.5"),
        exemptions=(
            "Alimentos sin preparar para consumo en el hogar",
            "Medicamentos recetados por medico licenciado",
            "Medicamentos de venta libre (OTC) en farmacia",
            "Articulos de manufactura destinados a exportacion",
            "Servicios medicos y hospitalarios",
            "Servicios educativos de instituciones acreditadas",
            "Servicios financieros regulados (intereses, dividendos)",
            "Propiedad vendida en transacciones de reorganizacion corporativa",
            "Articulos introducidos bajo Bond en zona de libre comercio",
            "Ventas al gobierno de Puerto Rico e instrumentalidades",
        ),
        conditions=(
            ("municipio_remision", "IVU estatal se remite al Gobierno Central de PR"),
            ("formulario_mensual", "SC 2915 — vence dia 20 de cada mes"),
            ("base_calculo", "Precio de venta o valor razonable en mercado, lo que sea mayor"),
            ("ivu_de_uso", "Aplica sobre compras fuera de PR traidas para uso local"),
        ),
    ),

    # -------------------------------------------------------------------------
    # IVU MUNICIPAL — 1.0%
    # Misma base legal que IVU estatal — tasa municipal separada
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="IVU_MUNICIPAL_PR_2015_V1",
        name="IVU Municipal Puerto Rico — 1.0%",
        description=(
            "Impuesto sobre Ventas y Uso municipal de Puerto Rico a una tasa del 1.0%. "
            "Se aplica sobre la misma base que el IVU estatal y se remite al municipio "
            "donde ocurrio la transaccion. Aplican las mismas exenciones que el IVU estatal."
        ),
        legal_reference=(
            "Codigo de Rentas Internas de Puerto Rico (CRIR), Ley 1-2011, "
            "Seccion 4010.01(b), enmendada por Ley Num. 72 del 29 de mayo de 2015. "
            "La tasa municipal de 1.0% se remite al fondo municipal del municipio "
            "donde se realizo la venta o se presto el servicio."
        ),
        source_url="https://www.hacienda.pr.gov/sites/default/files/ley1-2011.pdf",
        published_date=date(2015, 5, 29),
        effective_date=date(2015, 10, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="IVU_MUNICIPAL",
        jurisdiction="PR",
        rate=Decimal("1.0"),
        exemptions=(
            "Alimentos sin preparar para consumo en el hogar",
            "Medicamentos recetados por medico licenciado",
            "Medicamentos de venta libre (OTC) en farmacia",
            "Articulos de manufactura destinados a exportacion",
            "Servicios medicos y hospitalarios",
            "Servicios educativos de instituciones acreditadas",
            "Servicios financieros regulados (intereses, dividendos)",
            "Ventas al gobierno de Puerto Rico e instrumentalidades",
        ),
        conditions=(
            ("remision", "Se remite al municipio donde ocurrio la transaccion"),
            ("tracking_municipio", "Sistema trackea municipio por punto de venta"),
            ("formulario_mensual", "SC 2915 incluye desglose por municipio — vence dia 20"),
            ("multi_municipio", "Operaciones multi-localizacion requieren reporte separado por municipio"),
            ("ivu_total", "IVU total = 10.5% estatal + 1.0% municipal = 11.5% total"),
        ),
    ),

    # -------------------------------------------------------------------------
    # FICA SOCIAL SECURITY — 2024 (tope $168,600) — EXPIRADA
    # Incluida como referencia historica para transacciones 2024
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="FICA_SS_2024_V1",
        name="FICA Social Security 2024 — 6.2% tope $168,600",
        description=(
            "Contribucion FICA de Seguro Social para ano fiscal 2024. "
            "Tasa de 6.2% tanto para empleado como para patrono, aplicable "
            "sobre salarios hasta el tope anual de $168,600. "
            "Aplica a empleados en Puerto Rico bajo el sistema federal."
        ),
        legal_reference=(
            "Internal Revenue Code (IRC) Sections 3101(a) y 3111(a). "
            "Social Security Administration: Notice of Wage Base for 2024. "
            "Federal Register Vol. 88. Tope de $168,600 para ano calendario 2024."
        ),
        source_url="https://www.ssa.gov/news/press/factsheets/colafacts2024.pdf",
        published_date=date(2023, 10, 12),
        effective_date=date(2024, 1, 1),
        expiry_date=date(2024, 12, 31),
        validated_by_cpa=True,
        validation_date=date(2024, 1, 10),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="FICA_SS",
        jurisdiction="PR_AND_FEDERAL",
        rate_employee=Decimal("6.2"),
        rate_employer=Decimal("6.2"),
        wage_base_limit=Decimal("168600"),
        conditions=(
            ("tope_anual", "Salarios sobre $168,600 no estan sujetos a SS en 2024"),
            ("acumulado", "El tope es acumulado anual — reinicia el 1 de enero"),
            ("formulario", "Reportado en 941-PR trimestral e W-2PR anual"),
            ("ambos_lados", "Empleado paga 6.2% + patrono paga 6.2% = 12.4% total"),
        ),
    ),

    # -------------------------------------------------------------------------
    # FICA SOCIAL SECURITY — 2025 (tope $176,100) — VIGENTE
    # Supersede la regla de 2024
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="FICA_SS_2025_V1",
        name="FICA Social Security 2025 — 6.2% tope $176,100",
        description=(
            "Contribucion FICA de Seguro Social para ano fiscal 2025. "
            "Tasa de 6.2% tanto para empleado como para patrono, aplicable "
            "sobre salarios hasta el nuevo tope anual de $176,100. "
            "El tope aumento de $168,600 (2024) a $176,100 (2025)."
        ),
        legal_reference=(
            "Internal Revenue Code (IRC) Sections 3101(a) y 3111(a). "
            "Social Security Administration: Notice of Wage Base for 2025. "
            "Federal Register Vol. 89. Tope de $176,100 para ano calendario 2025."
        ),
        source_url="https://www.ssa.gov/news/press/factsheets/colafacts2025.pdf",
        published_date=date(2024, 10, 10),
        effective_date=date(2025, 1, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 10),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id="FICA_SS_2024_V1",
        tax_type="FICA_SS",
        jurisdiction="PR_AND_FEDERAL",
        rate_employee=Decimal("6.2"),
        rate_employer=Decimal("6.2"),
        wage_base_limit=Decimal("176100"),
        conditions=(
            ("tope_anual", "Salarios sobre $176,100 no estan sujetos a SS en 2025"),
            ("acumulado", "El tope es acumulado anual — reinicia el 1 de enero"),
            ("formulario", "Reportado en 941-PR trimestral e W-2PR anual"),
            ("ambos_lados", "Empleado paga 6.2% + patrono paga 6.2% = 12.4% total"),
            ("cambio_vs_2024", "Aumento de $168,600 (2024) a $176,100 (2025) = +$7,500"),
        ),
    ),

    # -------------------------------------------------------------------------
    # FICA MEDICARE — 1.45% + 0.9% adicional sobre $200K
    # Vigente desde 2013 (ACA Additional Medicare Tax)
    # Sin tope salarial — aplica sobre todos los salarios
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="FICA_MEDICARE_2013_V1",
        name="FICA Medicare — 1.45% + 0.9% adicional sobre $200K",
        description=(
            "Contribucion FICA de Medicare. Tasa base de 1.45% sobre todos los salarios "
            "sin tope. Patrono paga 1.45% adicional (total patrono 1.45%). "
            "Adicionalmente, empleados con salarios sobre $200,000 (soltero) o "
            "$250,000 (casado declaracion conjunta) pagan 0.9% adicional bajo la "
            "Additional Medicare Tax del Affordable Care Act."
        ),
        legal_reference=(
            "Internal Revenue Code (IRC) Sections 3101(b) y 3111(b) — tasa base 1.45%. "
            "IRC Section 3102(f) — Additional Medicare Tax de 0.9% implementado por "
            "el Affordable Care Act (ACA), Public Law 111-148, efectivo 1 enero 2013. "
            "IRS Notice 2013-45 y Revenue Ruling 2013-17."
        ),
        source_url="https://www.irs.gov/businesses/small-businesses-self-employed/questions-and-answers-for-the-additional-medicare-tax",
        published_date=date(2010, 3, 23),
        effective_date=date(2013, 1, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="FICA_MEDICARE",
        jurisdiction="PR_AND_FEDERAL",
        rate_employee=Decimal("1.45"),
        rate_employer=Decimal("1.45"),
        wage_base_limit=None,
        additional_rate_threshold=Decimal("200000"),
        additional_rate=Decimal("0.9"),
        conditions=(
            ("sin_tope", "Medicare no tiene tope salarial anual — aplica sobre 100% del salario"),
            ("adicional_soltero", "0.9% adicional: umbral $200,000 para soltero/cabeza de familia"),
            ("adicional_casado", "0.9% adicional: umbral $250,000 para casados declaracion conjunta"),
            ("adicional_casado_sep", "0.9% adicional: umbral $125,000 para casados declaracion separada"),
            ("retencion_patrono", "Patrono debe retener el 0.9% adicional cuando el salario YTD supera $200K"),
            ("formulario", "Reportado en 941-PR trimestral, W-2PR anual, Form 8959 del empleado"),
            ("total_empleado", "Total empleado sobre $200K: 1.45% + 0.9% = 2.35%"),
        ),
    ),

    # -------------------------------------------------------------------------
    # FUTA — 6.0% con credito 5.4% (tasa neta efectiva 0.6%)
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="FUTA_FEDERAL_2011_V1",
        name="FUTA Federal — 6.0% bruto / 0.6% neto con credito 5.4%",
        description=(
            "Federal Unemployment Tax Act (FUTA). Tasa bruta de 6.0% sobre los "
            "primeros $7,000 de salario por empleado por ano. El patrono recibe "
            "un credito de hasta 5.4% si el estado/territorio tiene su SUTA al dia, "
            "resultando en una tasa neta efectiva de 0.6% sobre los primeros $7,000. "
            "Puerto Rico califica para el credito completo cuando el SUTA-PR esta vigente. "
            "Solo lo paga el patrono — no se descuenta del salario del empleado."
        ),
        legal_reference=(
            "Internal Revenue Code (IRC) Sections 3301 (tasa bruta 6.0%) y "
            "3302 (credito contra SUTA estatal hasta 5.4%). "
            "IRS Publication 15 (Circular E) — Employer's Tax Guide. "
            "Tope de $7,000 establecido por IRC Section 3306(b)(1)."
        ),
        source_url="https://www.irs.gov/pub/irs-pdf/p15.pdf",
        published_date=date(1939, 1, 1),
        effective_date=date(2011, 1, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="FUTA",
        jurisdiction="FEDERAL",
        rate=Decimal("6.0"),
        credit_rate=Decimal("5.4"),
        wage_base_limit=Decimal("7000"),
        conditions=(
            ("tasa_neta", "Tasa neta efectiva = 6.0% - 5.4% credito = 0.6% si SUTA-PR al dia"),
            ("tope_por_empleado", "Aplica solo sobre los primeros $7,000 de salario bruto por empleado"),
            ("solo_patrono", "FUTA es contribucion del patrono — nunca se descuenta al empleado"),
            ("credito_condicionado", "Credito 5.4% requiere que PR no tenga prestamos federales pendientes"),
            ("formulario", "Form 940 anual — vence 31 de enero del ano siguiente"),
            ("pagos_trimestrales", "Si FUTA acumulada supera $500 en el trimestre, pago trimestral requerido"),
        ),
    ),

    # -------------------------------------------------------------------------
    # GAAP CAPITALIZACION — Umbral de activos fijos
    # ASC 360-10 + IRS Safe Harbor Rev. Proc. 2015-20
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="GAAP_CAPITALIZACION_PR_2016_V1",
        name="GAAP Umbral de Capitalizacion de Activos Fijos — $2,500",
        description=(
            "Umbral minimo para capitalizar una adquisicion como activo fijo en lugar "
            "de registrarla como gasto del periodo. Bajo el safe harbor del IRS, "
            "empresas sin estado financiero auditado pueden usar $2,500 por articulo "
            "o factura. Empresas con estado financiero auditado pueden usar $5,000. "
            "Para PR, aplica sobre GAAP (ASC 360-10) y las reglas del CRIR para "
            "depreciacion. El umbral es una politica contable — debe documentarse y "
            "aplicarse consistentemente."
        ),
        legal_reference=(
            "FASB ASC 360-10 — Property, Plant and Equipment. "
            "IRS Revenue Procedure 2015-20 — De Minimis Safe Harbor Election. "
            "IRS Treasury Regulation 1.263(a)-1(f) — De minimis safe harbor. "
            "Codigo de Rentas Internas de Puerto Rico (CRIR), Secciones 1040.04 "
            "y 1041.01 — Depreciacion y agotamiento."
        ),
        source_url="https://www.irs.gov/pub/irs-drop/rp-15-20.pdf",
        published_date=date(2015, 2, 13),
        effective_date=date(2016, 1, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="GAAP_CAPITALIZATION",
        jurisdiction="PR_AND_FEDERAL",
        capitalization_threshold=Decimal("2500"),
        conditions=(
            ("umbral_sin_auditoria", "$2,500 por articulo/factura para empresas sin estado auditado"),
            ("umbral_con_auditoria", "$5,000 por articulo/factura para empresas con estado auditado"),
            ("vida_util", "Activo debe tener vida util mayor a 1 ano para capitalizarse"),
            ("consistencia", "Politica de capitalizacion debe aplicarse consistentemente"),
            ("documentacion", "Election del safe harbor debe documentarse en declaracion de impuestos"),
            ("grupos_similares", "Articulos similares pueden agruparse para evaluar el umbral"),
            ("mejoras_vs_reparaciones", "Mejoras que extienden vida util se capitalizan — reparaciones se gastan"),
        ),
    ),

    # -------------------------------------------------------------------------
    # DEDUCCIONES PR — Categorias principales de gastos deducibles
    # CRIR Seccion 1033.01 et seq.
    # -------------------------------------------------------------------------
    TaxRule(
        rule_id="DEDUCIBLES_PR_CORPORATIVO_2011_V1",
        name="Gastos Deducibles — Corporaciones y LLC en Puerto Rico",
        description=(
            "Categorias de gastos ordinarios y necesarios deducibles del ingreso bruto "
            "para corporaciones y entidades de conducto en Puerto Rico. "
            "Un gasto es deducible si es ordinario (comun en el negocio), necesario "
            "(util y apropiado), y pagado o incurrido durante el ano tributario. "
            "Las categorias listadas son las principales reconocidas por el CRIR y "
            "validadas bajo practica contable estandar en PR."
        ),
        legal_reference=(
            "Codigo de Rentas Internas de Puerto Rico (CRIR), Ley 1-2011, "
            "Secciones 1033.01 (gastos ordinarios y necesarios), "
            "1033.02 (intereses), 1033.04 (impuestos), 1033.07 (depreciacion), "
            "1033.08 (gastos de investigacion y experimentacion), "
            "1033.09 (perdidas ordinarias), 1033.17 (gastos de negocio). "
            "Reglamento 8303 de Hacienda PR — Normas para la deduccion de gastos."
        ),
        source_url="https://www.hacienda.pr.gov/sites/default/files/ley1-2011.pdf",
        published_date=date(2011, 1, 31),
        effective_date=date(2011, 7, 1),
        expiry_date=None,
        validated_by_cpa=True,
        validation_date=date(2025, 1, 15),
        validating_cpa_license="CPA-PR-001",
        times_applied=0,
        has_controversy=False,
        controversy_notes=None,
        version=1,
        supersedes_rule_id=None,
        tax_type="DEDUCTIBLE_PR",
        jurisdiction="PR",
        deductible_categories=(
            "Salarios y compensacion razonable a empleados (CRIR 1033.01)",
            "Renta pagada por uso de propiedad en el negocio (CRIR 1033.01)",
            "Intereses sobre deudas del negocio (CRIR 1033.02)",
            "Impuestos estatales y municipales pagados (CRIR 1033.04)",
            "Perdidas ordinarias del negocio no cubiertas por seguro (CRIR 1033.09)",
            "Depreciacion de activos fijos bajo metodos CRIR (CRIR 1033.07)",
            "Amortizacion de intangibles — goodwill, patentes, etc.",
            "Gastos de publicidad y mercadeo directamente relacionados al negocio",
            "Primas de seguro de negocio (responsabilidad, propiedad, vida empleados)",
            "Gastos de viaje de negocios — transporte, hospedaje, comidas 50%",
            "Gastos de educacion para mantener o mejorar habilidades del negocio",
            "Contribuciones a planes de retiro calificados (401k, IRA SIMPLE)",
            "Honorarios profesionales — legal, contable, consultoría",
            "Servicios de tecnologia y software usados en el negocio",
            "Suministros y materiales de oficina consumidos en el ano",
            "Gastos de investigacion y experimentacion (CRIR 1033.08)",
            "Contribuciones caritativas — hasta 10% del ingreso neto corporativo",
            "Deuda incobrable — metodo directo con documentacion (CRIR 1033.10)",
            "Costo de bienes vendidos (COGS) — inventario directamente",
            "Gastos de automovil de negocio — tasa milla IRS o gastos reales",
        ),
        conditions=(
            ("ordinario_necesario", "Gasto debe ser ordinario Y necesario para el negocio especifico"),
            ("documentacion", "Cada gasto requiere recibo, factura o comprobante — sin documentacion no es deducible"),
            ("comidas_limite", "Comidas de negocio deducibles al 50% — entretenimiento NO es deducible desde 2018"),
            ("auto_mixto", "Vehiculo de uso mixto (negocio + personal) requiere log de mileaje"),
            ("gastos_capital", "Gastos de capital no se deducen inmediatamente — se deprecian"),
            ("gastos_personales", "Gastos personales del dueno NO son deducibles aunque se paguen con fondos de la empresa"),
        ),
    ),
]


# ---------------------------------------------------------------------------
# REGISTRO INMUTABLE
# MappingProxyType garantiza que el dict no puede ser modificado desde afuera.
# Las listas internas tambien son tuplas (inmutables).
#
# Estructura: { rule_id: (TaxRule, ...) }  — todas las versiones de cada regla
# ---------------------------------------------------------------------------

def _build_registry(rules: list[TaxRule]) -> MappingProxyType:
    """Construye el registro inmutable agrupando reglas por tax_type."""
    index: dict[str, list[TaxRule]] = {}
    for rule in rules:
        index.setdefault(rule.rule_id, []).append(rule)
    # Ordenar por effective_date dentro de cada grupo
    for key in index:
        index[key].sort(key=lambda r: r.effective_date)
    # Convertir listas a tuplas para inmutabilidad
    return MappingProxyType({k: tuple(v) for k, v in index.items()})


# Este es el unico punto de acceso al registro.
# Es de tipo Final para que mypy/pyright rechacen cualquier reasignacion.
RULES_REGISTRY: Final[MappingProxyType] = _build_registry(_INITIAL_RULES)

# Indice secundario por tax_type para busquedas rapidas
_BY_TAX_TYPE: Final[MappingProxyType] = MappingProxyType(
    {
        tax_type: tuple(
            rule
            for rules in RULES_REGISTRY.values()
            for rule in rules
            if rule.tax_type == tax_type
        )
        for tax_type in {r.tax_type for rules in RULES_REGISTRY.values() for r in rules}
    }
)


def get_all_rule_ids() -> tuple[str, ...]:
    """Retorna todos los rule_ids registrados. Solo lectura."""
    return tuple(RULES_REGISTRY.keys())


def get_rules_by_tax_type(tax_type: str) -> tuple[TaxRule, ...]:
    """Retorna todas las versiones de reglas de un tipo especifico. Solo lectura."""
    return _BY_TAX_TYPE.get(tax_type, ())


def _get_all_versions(rule_id: str) -> tuple[TaxRule, ...]:
    """Uso interno: retorna todas las versiones de una regla."""
    return RULES_REGISTRY.get(rule_id, ())
