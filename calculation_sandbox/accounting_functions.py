# =============================================================================
# calculation_sandbox/accounting_functions.py
# Funciones contables pre-aprobadas disponibles dentro del sandbox.
#
# GARANTIAS DE DISENO:
#   - Estas funciones son el UNICO canal de calculo fiscal para el LLM.
#   - Todas usan Decimal — nunca float.
#   - Cada funcion obtiene tasas del modulo tax_rules — nunca hardcodea.
#   - Son inyectadas en el namespace del sandbox como builtins seguros.
# =============================================================================

from __future__ import annotations

import sys
import os

# Asegura que el paquete raiz este en el path cuando este modulo se importa
# directamente desde el sandbox sin el contexto del proyecto
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from tax_rules import get_rule, calculate_ivu


# ---------------------------------------------------------------------------
# calc_ivu — IVU de Puerto Rico (estatal + municipal)
# ---------------------------------------------------------------------------

def calc_ivu(
    base_amount: Decimal,
    rate: Optional[Decimal],          # None = usar tasa vigente del tax_rules
    calc_date: date,
    include_municipal: bool = True,
) -> dict:
    """
    Calcula IVU de Puerto Rico usando tasas del modulo tax_rules.

    Args:
        base_amount:       Monto base gravable (Decimal).
        rate:              Tasa override (None = usar tasa vigente automaticamente).
        calc_date:         Fecha del calculo para seleccion de tasa correcta.
        include_municipal: Incluir componente municipal 1.0% (default True).

    Returns:
        dict con: base, ivu_estatal, ivu_municipal, total_ivu, total_con_ivu,
                  rate_estatal, rate_municipal, rule_ids, exento.
    """
    if not isinstance(base_amount, Decimal):
        base_amount = Decimal(str(base_amount))

    if base_amount < Decimal("0"):
        raise ValueError(
            f"calc_ivu: base_amount no puede ser negativo ({base_amount}). "
            "Para creditos de entrada use un monto positivo y registre como credito."
        )

    result = calculate_ivu(
        base_amount=base_amount,
        query_date=calc_date,
        include_municipal=include_municipal,
    )

    return {
        "base":          result.base_amount,
        "ivu_estatal":   result.ivu_estatal_amount,
        "ivu_municipal": result.ivu_municipal_amount,
        "total_ivu":     result.total_ivu,
        "total_con_ivu": result.total_with_ivu,
        "rate_estatal":  result.ivu_estatal_rate,
        "rate_municipal": result.ivu_municipal_rate,
        "rule_id_estatal":   result.rule_id_estatal,
        "rule_id_municipal": result.rule_id_municipal,
        "exento": False,
        "calc_hash": result.calc_hash,
    }


def calc_ivu_exento(base_amount: Decimal, calc_date: date, razon: str) -> dict:
    """
    Registra una transaccion exenta de IVU con razon documentada.
    Retorna IVU = 0 con trazabilidad completa.
    """
    if not isinstance(base_amount, Decimal):
        base_amount = Decimal(str(base_amount))

    return {
        "base":          base_amount,
        "ivu_estatal":   Decimal("0.00"),
        "ivu_municipal": Decimal("0.00"),
        "total_ivu":     Decimal("0.00"),
        "total_con_ivu": base_amount,
        "rate_estatal":  Decimal("0.00"),
        "rate_municipal": Decimal("0.00"),
        "rule_id_estatal":   "EXENTO",
        "rule_id_municipal": "EXENTO",
        "exento": True,
        "razon_exencion": razon,
        "calc_hash": "EXENTO",
    }


# ---------------------------------------------------------------------------
# calc_fica — FICA (Social Security + Medicare) para Puerto Rico
# ---------------------------------------------------------------------------

def calc_fica(
    gross_pay: Decimal,
    ytd_ss: Decimal,
    ytd_medicare: Decimal,
    calc_year: int,
) -> dict:
    """
    Calcula retencion FICA (SS + Medicare) para un periodo de nomina.

    Args:
        gross_pay:    Pago bruto del periodo.
        ytd_ss:       SS acumulado en el ano hasta el periodo ANTERIOR.
        ytd_medicare: Medicare acumulado hasta el periodo ANTERIOR.
        calc_year:    Ano del calculo (determina que regla FICA usar).

    Returns:
        dict con: ss_employee, ss_employer, medicare_employee,
                  medicare_employer, medicare_adicional, total_employee,
                  total_employer, rule_ids, tope_ss_aplicado.
    """
    if not isinstance(gross_pay, Decimal):
        gross_pay = Decimal(str(gross_pay))
    if not isinstance(ytd_ss, Decimal):
        ytd_ss = Decimal(str(ytd_ss))
    if not isinstance(ytd_medicare, Decimal):
        ytd_medicare = Decimal(str(ytd_medicare))

    if gross_pay < Decimal("0"):
        raise ValueError(f"calc_fica: gross_pay no puede ser negativo ({gross_pay}).")
    if ytd_ss < Decimal("0") or ytd_medicare < Decimal("0"):
        raise ValueError("calc_fica: YTD acumulados no pueden ser negativos.")

    query_date = date(calc_year, 6, 15)   # mitad del anno para lookup

    # Obtener reglas vigentes
    ss_rule = get_rule("FICA_SS", query_date)
    if ss_rule is None:
        # Intentar con version explicita del anno
        ss_rule = get_rule(f"FICA_SS_{calc_year}_V1", query_date)
    if ss_rule is None:
        raise ValueError(
            f"calc_fica: No se encontro regla FICA SS vigente en {calc_year}. "
            "Actualice el modulo tax_rules con la regla del anno correspondiente."
        )

    medicare_rule = get_rule("FICA_MEDICARE_2013_V1", query_date)
    if medicare_rule is None:
        raise ValueError("calc_fica: No se encontro regla FICA Medicare.")

    # Las tasas en tax_rules estan en porcentaje (6.2 = 6.2%) — convertir a decimal
    _HUNDRED = Decimal("100")
    ss_rate_employee = ss_rule.rate_employee / _HUNDRED     # 6.2 → 0.062
    ss_rate_employer = ss_rule.rate_employer / _HUNDRED     # 6.2 → 0.062
    wage_base = ss_rule.wage_base_limit  # e.g. Decimal("176100") para 2025

    medicare_rate_employee = medicare_rule.rate_employee / _HUNDRED  # 1.45 → 0.0145
    medicare_rate_employer = medicare_rule.rate_employer / _HUNDRED
    additional_rate = medicare_rule.additional_rate / _HUNDRED       # 0.9 → 0.009
    additional_threshold = medicare_rule.additional_rate_threshold   # 200000

    # Social Security — se detiene al alcanzar el tope
    ss_taxable = max(
        Decimal("0"),
        min(gross_pay, wage_base - ytd_ss) if wage_base else gross_pay,
    )
    tope_ss_aplicado = (ytd_ss + gross_pay) > wage_base if wage_base else False

    ss_employee = (ss_taxable * ss_rate_employee).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    ss_employer = (ss_taxable * ss_rate_employer).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Medicare base — sin tope
    mc_base_employee = (gross_pay * medicare_rate_employee).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    mc_base_employer = (gross_pay * medicare_rate_employer).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Medicare adicional 0.9% cuando YTD supera el umbral (solo employee)
    ytd_mc_after = ytd_medicare + gross_pay
    mc_adicional = Decimal("0.00")
    if additional_threshold and ytd_mc_after > additional_threshold:
        adicional_base = min(gross_pay, ytd_mc_after - additional_threshold)
        mc_adicional = (adicional_base * additional_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    total_employee = ss_employee + mc_base_employee + mc_adicional
    total_employer  = ss_employer + mc_base_employer

    return {
        "ss_employee":        ss_employee,
        "ss_employer":        ss_employer,
        "medicare_employee":  mc_base_employee,
        "medicare_employer":  mc_base_employer,
        "medicare_adicional": mc_adicional,
        "total_employee":     total_employee,
        "total_employer":     total_employer,
        "tope_ss_aplicado":   tope_ss_aplicado,
        "ss_taxable_base":    ss_taxable,
        "rule_id_ss":         ss_rule.rule_id,
        "rule_id_medicare":   medicare_rule.rule_id,
        "wage_base_limit":    wage_base,
    }


# ---------------------------------------------------------------------------
# calc_futa — FUTA (Federal Unemployment Tax) con credito estatal PR
# ---------------------------------------------------------------------------

def calc_futa(
    gross_pay: Decimal,
    ytd_futa: Decimal,
    state_credit: Optional[Decimal] = None,
    calc_date: Optional[date] = None,
) -> dict:
    """
    Calcula FUTA para un periodo de nomina.

    FUTA bruta: 6.0% sobre primeros $7,000 del salario anual.
    Con credito estatal maximo (SUTA-PR pagado): tasa neta 0.6%.

    Args:
        gross_pay:    Pago bruto del periodo.
        ytd_futa:     Base FUTA acumulada hasta el periodo ANTERIOR.
        state_credit: Credito SUTA estatal (None = asumir credito maximo 5.4%).
        calc_date:    Fecha del calculo (None = hoy).

    Returns:
        dict con: futa_bruta, state_credit, futa_neta, taxable_base,
                  tope_aplicado, rule_id.
    """
    if not isinstance(gross_pay, Decimal):
        gross_pay = Decimal(str(gross_pay))
    if not isinstance(ytd_futa, Decimal):
        ytd_futa = Decimal(str(ytd_futa))

    if gross_pay < Decimal("0"):
        raise ValueError(f"calc_futa: gross_pay no puede ser negativo ({gross_pay}).")

    if calc_date is None:
        from datetime import date as _date
        calc_date = _date.today()

    futa_rule = get_rule("FUTA_FEDERAL_2011_V1", calc_date)
    if futa_rule is None:
        raise ValueError("calc_futa: No se encontro regla FUTA vigente.")

    # Tasa en tax_rules en porcentaje (6.0 = 6.0%) — convertir a decimal
    futa_gross_rate = futa_rule.rate / Decimal("100")   # 6.0 → 0.06
    wage_cap = futa_rule.wage_base_limit                # Decimal("7000")

    # Base gravable — solo hasta el tope de $7,000
    taxable_base = max(
        Decimal("0"),
        min(gross_pay, wage_cap - ytd_futa),
    )
    tope_aplicado = (ytd_futa + gross_pay) > wage_cap

    futa_bruta = (taxable_base * futa_gross_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    # Credito estatal: maximo 5.4% si SUTA-PR esta al dia
    max_state_credit_rate = Decimal("0.054")
    if state_credit is None:
        # Asumir credito completo — caso tipico para PR con SUTA al dia
        applied_credit = (taxable_base * max_state_credit_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        if not isinstance(state_credit, Decimal):
            state_credit = Decimal(str(state_credit))
        applied_credit = min(state_credit, futa_bruta)

    futa_neta = max(Decimal("0.00"), futa_bruta - applied_credit)

    return {
        "futa_bruta":     futa_bruta,
        "state_credit":   applied_credit,
        "futa_neta":      futa_neta,
        "taxable_base":   taxable_base,
        "tope_aplicado":  tope_aplicado,
        "wage_cap":       wage_cap,
        "rule_id":        futa_rule.rule_id,
    }


# ---------------------------------------------------------------------------
# calc_retention — Retencion en la fuente (withholding)
# ---------------------------------------------------------------------------

_RETENTION_RATES: dict[str, Decimal] = {
    # Retencion a no residentes sobre servicios en PR
    "NO_RESIDENTE_SERVICIOS": Decimal("0.29"),
    # Retencion sobre intereses
    "INTERESES":              Decimal("0.17"),
    # Retencion sobre dividendos
    "DIVIDENDOS":             Decimal("0.15"),
    # Retencion sobre pagos de honorarios profesionales (PR)
    "HONORARIOS_PR":          Decimal("0.07"),
    # Retencion sobre regalias
    "REGALIAS":               Decimal("0.29"),
    # Retencion sobre ganancias de juego
    "JUEGO":                  Decimal("0.20"),
    # Retencion sobre pagos a contratistas (Schedule K PR)
    "CONTRATISTAS_PR":        Decimal("0.10"),
}

def calc_retention(
    amount: Decimal,
    retention_type: str,
    calc_date: date,
    override_rate: Optional[Decimal] = None,
) -> dict:
    """
    Calcula retencion en la fuente segun el tipo de pago.

    Args:
        amount:         Monto bruto sujeto a retencion.
        retention_type: Tipo de retencion (ver _RETENTION_RATES).
        calc_date:      Fecha del calculo.
        override_rate:  Tasa override con justificacion (requiere CPA review).

    Returns:
        dict con: monto_bruto, tasa, retencion, neto, retention_type, rule_ref.
    """
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))

    if amount < Decimal("0"):
        raise ValueError(
            f"calc_retention: amount no puede ser negativo ({amount}). "
            "Para notas de credito, use el monto positivo y registre el tipo."
        )

    retention_type_upper = retention_type.upper()
    if override_rate is not None:
        if not isinstance(override_rate, Decimal):
            override_rate = Decimal(str(override_rate))
        if override_rate < Decimal("0") or override_rate > Decimal("1"):
            raise ValueError(
                f"calc_retention: override_rate debe estar entre 0.0 y 1.0 ({override_rate})."
            )
        rate = override_rate
        rule_ref = f"OVERRIDE_{retention_type_upper} — requiere CPA review"
    else:
        if retention_type_upper not in _RETENTION_RATES:
            known = list(_RETENTION_RATES.keys())
            raise ValueError(
                f"calc_retention: tipo '{retention_type}' no reconocido. "
                f"Tipos validos: {known}"
            )
        rate = _RETENTION_RATES[retention_type_upper]
        rule_ref = f"RETENCION_{retention_type_upper}_PR"

    retencion = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neto = amount - retencion

    return {
        "monto_bruto":    amount,
        "tasa":           rate,
        "retencion":      retencion,
        "neto":           neto,
        "retention_type": retention_type_upper,
        "rule_ref":       rule_ref,
    }


# ---------------------------------------------------------------------------
# calc_depreciation — Depreciacion de activos fijos
# ---------------------------------------------------------------------------

_USEFUL_LIFE_DEFAULTS: dict[str, int] = {
    "EDIFICIO":       39,   # MACRS 39 anos (inmuebles comerciales)
    "MEJORAS":        15,   # MACRS 15 anos (mejoras a arrendamiento)
    "VEHICULO":        5,   # MACRS 5 anos
    "EQUIPO_OFICINA":  7,   # MACRS 7 anos
    "COMPUTADORA":     5,
    "MUEBLES":         7,
    "MAQUINARIA":      7,
    "EQUIPO_MEDICO":   5,
}

def calc_depreciation(
    cost: Decimal,
    useful_life: int,
    method: str,
    date_placed: date,
    salvage_value: Decimal = Decimal("0.00"),
    period_date: Optional[date] = None,
) -> dict:
    """
    Calcula depreciacion de un activo para un periodo.

    Metodos soportados:
        LINEA_RECTA    — Straight-line (SL): uniforme durante la vida util.
        DOBLE_SALDO    — Double declining balance (DDB).
        SUMA_DIGITOS   — Sum-of-years-digits (SYD).

    Args:
        cost:          Costo original del activo.
        useful_life:   Vida util en anos.
        method:        Metodo de depreciacion (LINEA_RECTA, DOBLE_SALDO, SUMA_DIGITOS).
        date_placed:   Fecha en que el activo fue puesto en servicio.
        salvage_value: Valor residual esperado (default $0.00).
        period_date:   Fecha del periodo de calculo (None = primer ano completo).

    Returns:
        dict con: depreciacion_anual, depreciacion_acumulada, valor_libro,
                  metodo, ano_de_vida, costo_original, valor_residual.
    """
    if not isinstance(cost, Decimal):
        cost = Decimal(str(cost))
    if not isinstance(salvage_value, Decimal):
        salvage_value = Decimal(str(salvage_value))

    if cost <= Decimal("0"):
        raise ValueError(f"calc_depreciation: cost debe ser positivo ({cost}).")
    if useful_life <= 0:
        raise ValueError(f"calc_depreciation: useful_life debe ser >= 1 ({useful_life}).")
    if salvage_value < Decimal("0"):
        raise ValueError("calc_depreciation: salvage_value no puede ser negativo.")
    if salvage_value >= cost:
        raise ValueError(
            f"calc_depreciation: salvage_value ({salvage_value}) >= cost ({cost})."
        )

    if period_date is None:
        period_date = date(date_placed.year + 1, 1, 1)

    # Calcular en que ano de vida util estamos
    year_of_life = max(1, (period_date.year - date_placed.year) + 1)
    year_of_life = min(year_of_life, useful_life)

    depreciable_base = cost - salvage_value
    method_upper = method.upper()

    if method_upper == "LINEA_RECTA":
        depreciacion_anual = (depreciable_base / Decimal(useful_life)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        depreciacion_acumulada = (depreciacion_anual * Decimal(year_of_life)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    elif method_upper == "DOBLE_SALDO":
        tasa_ddb = Decimal("2") / Decimal(useful_life)
        valor_libro_inicio = cost
        depreciacion_acumulada = Decimal("0.00")
        depreciacion_anual = Decimal("0.00")
        for yr in range(1, year_of_life + 1):
            dep = (valor_libro_inicio * tasa_ddb).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            # No depreciar por debajo del valor residual
            dep = min(dep, valor_libro_inicio - salvage_value)
            depreciacion_anual = dep
            depreciacion_acumulada += dep
            valor_libro_inicio -= dep

    elif method_upper == "SUMA_DIGITOS":
        n = useful_life
        suma_digitos = Decimal(n * (n + 1) // 2)
        anos_restantes = Decimal(useful_life - year_of_life + 1)
        depreciacion_anual = (depreciable_base * anos_restantes / suma_digitos).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # Acumulado: suma de depreciaciones de anos 1..year_of_life
        depreciacion_acumulada = Decimal("0.00")
        for yr in range(1, year_of_life + 1):
            anos_rest_yr = Decimal(useful_life - yr + 1)
            dep_yr = (depreciable_base * anos_rest_yr / suma_digitos).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            depreciacion_acumulada += dep_yr

    else:
        raise ValueError(
            f"calc_depreciation: metodo '{method}' no soportado. "
            "Metodos validos: LINEA_RECTA, DOBLE_SALDO, SUMA_DIGITOS"
        )

    valor_libro = cost - depreciacion_acumulada

    return {
        "depreciacion_anual":      depreciacion_anual,
        "depreciacion_acumulada":  depreciacion_acumulada,
        "valor_libro":             valor_libro,
        "metodo":                  method_upper,
        "ano_de_vida":             year_of_life,
        "vida_util":               useful_life,
        "costo_original":          cost,
        "valor_residual":          salvage_value,
    }


# ---------------------------------------------------------------------------
# Namespace publico — lo que el sandbox inyecta en el entorno restringido
# ---------------------------------------------------------------------------

ACCOUNTING_FUNCTIONS: dict[str, object] = {
    "calc_ivu":          calc_ivu,
    "calc_ivu_exento":   calc_ivu_exento,
    "calc_fica":         calc_fica,
    "calc_futa":         calc_futa,
    "calc_retention":    calc_retention,
    "calc_depreciation": calc_depreciation,
}
