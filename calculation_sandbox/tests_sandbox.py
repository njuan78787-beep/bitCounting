# =============================================================================
# calculation_sandbox/tests_sandbox.py
# Tests unitarios del sandbox de calculo fiscal.
#
# Casos edge cubiertos:
#   - Montos cero y negativos
#   - Fechas de cambio de tasa FICA (2024 → 2025)
#   - Topes de SS (wage_base_limit)
#   - Exenciones IVU
#   - Imports prohibidos, eval, exec, loops infinitos
#   - Codigo sin 'result', codigo con error de sintaxis
#   - calc_hash determinismo y unicidad
# =============================================================================

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from decimal import Decimal

from calculation_sandbox import CalculationSandbox, CodeValidator, ValidationError


def _run(label: str, condition: bool, detail: str = "") -> tuple[bool, str]:
    status = "PASSED" if condition else "FAILED"
    msg = f"  TEST {label}: {status}"
    if detail and not condition:
        msg += f"\n    → {detail}"
    print(msg)
    return condition, msg


# ===========================================================================
# GRUPO 1 — Ejecucion basica del sandbox
# ===========================================================================

def test_ivu_basico():
    """calc_ivu sobre $1,000 → $115.00 total IVU (10.5% + 1.0%)."""
    sb = CalculationSandbox()
    code = """
base = Decimal("1000.00")
r = calc_ivu(base, None, date(2025, 3, 15), True)
result = r
"""
    out = sb.execute(code)
    ok = (
        out.success
        and out.result_value is not None
        and out.result_value["total_ivu"] == "115.00"
    )
    return _run("01", ok, f"success={out.success} error={out.error} val={out.result_value}")


def test_ivu_solo_estatal():
    """calc_ivu sin municipio → solo 10.5%."""
    sb = CalculationSandbox()
    code = """
base = Decimal("1000.00")
r = calc_ivu(base, None, date(2025, 1, 1), False)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["total_ivu"] == "105.00"
    return _run("02", ok, f"error={out.error} val={out.result_value}")


def test_ivu_monto_cero():
    """IVU sobre $0 → $0 sin error."""
    sb = CalculationSandbox()
    code = """
r = calc_ivu(Decimal("0.00"), None, date(2025, 6, 1), True)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["total_ivu"] == "0.00"
    return _run("03", ok, f"error={out.error}")


def test_ivu_monto_negativo_rechazado():
    """calc_ivu con monto negativo → ValueError encapsulado."""
    sb = CalculationSandbox()
    code = """
r = calc_ivu(Decimal("-500.00"), None, date(2025, 6, 1), True)
result = r
"""
    out = sb.execute(code)
    ok = not out.success and "negativo" in (out.error or "").lower()
    return _run("04", ok, f"success={out.success} error={out.error}")


def test_ivu_exento():
    """calc_ivu_exento registra IVU=0 con razon documentada."""
    sb = CalculationSandbox()
    code = """
r = calc_ivu_exento(Decimal("500.00"), date(2025, 3, 1), "Medicamentos recetados")
result = r
"""
    out = sb.execute(code)
    ok = (
        out.success
        and out.result_value["exento"] is True
        and out.result_value["total_ivu"] == "0.00"
        and "razon_exencion" in out.result_value
    )
    return _run("05", ok, f"error={out.error} val={out.result_value}")


# ===========================================================================
# GRUPO 2 — FICA (SS + Medicare)
# ===========================================================================

def test_fica_ss_normal():
    """SS employee = 6.2% de gross_pay cuando YTD < tope."""
    sb = CalculationSandbox()
    code = """
r = calc_fica(
    gross_pay=Decimal("1000.00"),
    ytd_ss=Decimal("0.00"),
    ytd_medicare=Decimal("0.00"),
    calc_year=2025,
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["ss_employee"] == "62.00"
    return _run("06", ok, f"error={out.error} val={out.result_value}")


def test_fica_medicare_normal():
    """Medicare employee = 1.45% de gross_pay."""
    sb = CalculationSandbox()
    code = """
r = calc_fica(
    gross_pay=Decimal("1000.00"),
    ytd_ss=Decimal("0.00"),
    ytd_medicare=Decimal("0.00"),
    calc_year=2025,
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["medicare_employee"] == "14.50"
    return _run("07", ok, f"error={out.error}")


def test_fica_ss_sobre_tope():
    """SS = $0 cuando YTD ya supero el tope de $176,100."""
    sb = CalculationSandbox()
    code = """
r = calc_fica(
    gross_pay=Decimal("5000.00"),
    ytd_ss=Decimal("180000.00"),
    ytd_medicare=Decimal("2000.00"),
    calc_year=2025,
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["ss_employee"] == "0.00"
    return _run("08", ok, f"error={out.error} val={out.result_value}")


def test_fica_ss_tope_parcial():
    """SS parcial cuando gross_pay cruza el tope durante el periodo."""
    sb = CalculationSandbox()
    code = """
# YTD = 175,600 — faltan $500 para el tope de $176,100
r = calc_fica(
    gross_pay=Decimal("2000.00"),
    ytd_ss=Decimal("175600.00"),
    ytd_medicare=Decimal("2000.00"),
    calc_year=2025,
)
result = r
"""
    out = sb.execute(code)
    # Solo $500 son gravables: $500 * 6.2% = $31.00
    ok = (
        out.success
        and out.result_value["ss_employee"] == "31.00"
        and out.result_value["tope_ss_aplicado"] is True
    )
    return _run("09", ok, f"error={out.error} val={out.result_value}")


def test_fica_medicare_adicional():
    """Medicare adicional 0.9% cuando YTD > $200,000."""
    sb = CalculationSandbox()
    code = """
r = calc_fica(
    gross_pay=Decimal("10000.00"),
    ytd_ss=Decimal("0.00"),
    ytd_medicare=Decimal("195000.00"),
    calc_year=2025,
)
result = r
"""
    out = sb.execute(code)
    # YTD despues = $205,000 → adicional sobre $5,000 = $5,000 * 0.9% = $45.00
    ok = out.success and out.result_value["medicare_adicional"] == "45.00"
    return _run("10", ok, f"error={out.error} val={out.result_value}")


# ===========================================================================
# GRUPO 3 — FUTA
# ===========================================================================

def test_futa_neta_sobre_primer_7000():
    """FUTA neta 0.6% sobre los primeros $7,000."""
    sb = CalculationSandbox()
    code = """
r = calc_futa(
    gross_pay=Decimal("3000.00"),
    ytd_futa=Decimal("0.00"),
)
result = r
"""
    out = sb.execute(code)
    # 3000 * 0.6% = $18.00
    ok = out.success and out.result_value["futa_neta"] == "18.00"
    return _run("11", ok, f"error={out.error} val={out.result_value}")


def test_futa_tope_aplicado():
    """FUTA = $0 cuando YTD ya supero $7,000."""
    sb = CalculationSandbox()
    code = """
r = calc_futa(
    gross_pay=Decimal("2000.00"),
    ytd_futa=Decimal("7000.00"),
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["futa_neta"] == "0.00"
    return _run("12", ok, f"error={out.error}")


def test_futa_tope_parcial():
    """FUTA parcial al cruzar el tope de $7,000."""
    sb = CalculationSandbox()
    code = """
r = calc_futa(
    gross_pay=Decimal("3000.00"),
    ytd_futa=Decimal("6000.00"),
)
result = r
"""
    out = sb.execute(code)
    # Solo $1,000 son gravables: $1,000 * 0.6% = $6.00
    ok = out.success and out.result_value["futa_neta"] == "6.00"
    return _run("13", ok, f"error={out.error} val={out.result_value}")


# ===========================================================================
# GRUPO 4 — Retencion
# ===========================================================================

def test_retencion_honorarios():
    """Retencion honorarios profesionales PR = 7%."""
    sb = CalculationSandbox()
    code = """
r = calc_retention(
    amount=Decimal("5000.00"),
    retention_type="HONORARIOS_PR",
    calc_date=date(2025, 4, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["retencion"] == "350.00"
    return _run("14", ok, f"error={out.error} val={out.result_value}")


def test_retencion_tipo_invalido():
    """Tipo de retencion desconocido → error encapsulado."""
    sb = CalculationSandbox()
    code = """
r = calc_retention(
    amount=Decimal("1000.00"),
    retention_type="TIPO_FANTASMA",
    calc_date=date(2025, 1, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = not out.success and "no reconocido" in (out.error or "")
    return _run("15", ok, f"error={out.error}")


def test_retencion_monto_cero():
    """Retencion sobre $0 → $0."""
    sb = CalculationSandbox()
    code = """
r = calc_retention(
    amount=Decimal("0.00"),
    retention_type="INTERESES",
    calc_date=date(2025, 1, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["retencion"] == "0.00"
    return _run("16", ok, f"error={out.error}")


# ===========================================================================
# GRUPO 5 — Depreciacion
# ===========================================================================

def test_depreciacion_linea_recta():
    """Linea recta: $10,000 / 10 anos = $1,000/ano."""
    sb = CalculationSandbox()
    code = """
r = calc_depreciation(
    cost=Decimal("10000.00"),
    useful_life=10,
    method="LINEA_RECTA",
    date_placed=date(2020, 1, 1),
    period_date=date(2025, 1, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = out.success and out.result_value["depreciacion_anual"] == "1000.00"
    return _run("17", ok, f"error={out.error} val={out.result_value}")


def test_depreciacion_doble_saldo():
    """Doble saldo: $10,000, 5 anos, ano 1 = $4,000."""
    sb = CalculationSandbox()
    code = """
r = calc_depreciation(
    cost=Decimal("10000.00"),
    useful_life=5,
    method="DOBLE_SALDO",
    date_placed=date(2025, 1, 1),
    period_date=date(2025, 6, 1),
)
result = r
"""
    out = sb.execute(code)
    # Ano 1: $10,000 * 40% = $4,000
    ok = out.success and out.result_value["depreciacion_anual"] == "4000.00"
    return _run("18", ok, f"error={out.error} val={out.result_value}")


def test_depreciacion_costo_cero():
    """Depreciacion con costo = $0 → ValueError."""
    sb = CalculationSandbox()
    code = """
r = calc_depreciation(
    cost=Decimal("0.00"),
    useful_life=5,
    method="LINEA_RECTA",
    date_placed=date(2024, 1, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = not out.success and "positivo" in (out.error or "").lower()
    return _run("19", ok, f"error={out.error}")


def test_depreciacion_metodo_invalido():
    """Metodo de depreciacion desconocido → error encapsulado."""
    sb = CalculationSandbox()
    code = """
r = calc_depreciation(
    cost=Decimal("5000.00"),
    useful_life=5,
    method="ACELERACION_MAGICA",
    date_placed=date(2024, 1, 1),
)
result = r
"""
    out = sb.execute(code)
    ok = not out.success and "no soportado" in (out.error or "").lower()
    return _run("20", ok, f"error={out.error}")


# ===========================================================================
# GRUPO 6 — Seguridad del sandbox (validador)
# ===========================================================================

def test_import_prohibido_os():
    """'import os' → ValidationError."""
    sb = CalculationSandbox()
    code = "import os\nresult = os.getcwd()"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("21", ok, f"error={out.error}")


def test_import_prohibido_requests():
    """'import requests' → ValidationError."""
    sb = CalculationSandbox()
    code = "import requests\nresult = requests.get('http://evil.com').text"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("22", ok, f"error={out.error}")


def test_eval_prohibido():
    """'eval(...)' → ValidationError."""
    sb = CalculationSandbox()
    code = "result = eval('1+1')"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("23", ok, f"error={out.error}")


def test_exec_prohibido():
    """'exec(...)' → ValidationError."""
    sb = CalculationSandbox()
    code = "exec('import os')\nresult = 1"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("24", ok, f"error={out.error}")


def test_open_prohibido():
    """'open(...)' → NameError en ejecucion (builtins deshabilitados)."""
    sb = CalculationSandbox()
    code = "f = open('/etc/passwd')\nresult = f.read()"
    out = sb.execute(code)
    # open no pasa el validator pero si pasara, falla en ejecucion sin builtins
    ok = not out.success
    return _run("25", ok, f"error={out.error}")


def test_while_true_sin_break():
    """'while True:' sin break → ValidationError."""
    sb = CalculationSandbox()
    code = "while True:\n    x = 1\nresult = x"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("26", ok, f"error={out.error}")


def test_acceso_dunder_prohibido():
    """Acceso a __class__ → ValidationError."""
    sb = CalculationSandbox()
    code = "x = Decimal('1.0').__class__\nresult = x"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("27", ok, f"error={out.error}")


def test_builtins_globals_prohibido():
    """Referencia a globals() → ValidationError."""
    sb = CalculationSandbox()
    code = "result = globals()"
    out = sb.execute(code)
    ok = not out.success and "ValidationError" in (out.error_type or "")
    return _run("28", ok, f"error={out.error}")


def test_import_from_math_permitido():
    """'from math import sqrt' → permitido."""
    sb = CalculationSandbox()
    code = "from math import sqrt\nresult = Decimal(str(sqrt(9)))"
    out = sb.execute(code)
    ok = out.success
    return _run("29", ok, f"error={out.error}")


def test_codigo_sin_result():
    """Codigo que no asigna 'result' → MissingResultVariable."""
    sb = CalculationSandbox()
    code = "x = Decimal('1.0') + Decimal('2.0')"
    out = sb.execute(code)
    ok = not out.success and "MissingResultVariable" in (out.error_type or "")
    return _run("30", ok, f"error={out.error}")


# ===========================================================================
# GRUPO 7 — Audit trail y trazabilidad
# ===========================================================================

def test_calc_hash_presente():
    """SandboxResult incluye calc_hash de 64 caracteres hex."""
    sb = CalculationSandbox()
    code = "result = calc_ivu(Decimal('500.00'), None, date(2025, 1, 1), True)"
    out = sb.execute(code)
    ok = out.success and len(out.calc_hash) == 64 and all(
        c in "0123456789abcdef" for c in out.calc_hash
    )
    return _run("31", ok, f"hash='{out.calc_hash}'")


def test_calc_hash_unico_por_ejecucion():
    """Dos ejecuciones del mismo codigo producen hashes distintos (timestamp diferente)."""
    sb = CalculationSandbox()
    code = "result = calc_ivu(Decimal('1000.00'), None, date(2025, 1, 1), True)"
    out1 = sb.execute(code)
    out2 = sb.execute(code)
    ok = out1.calc_hash != out2.calc_hash
    return _run("32", ok, f"hash1={out1.calc_hash[:8]}... hash2={out2.calc_hash[:8]}...")


def test_result_id_unico():
    """Cada SandboxResult tiene un result_id UUID unico."""
    sb = CalculationSandbox()
    code = "result = Decimal('1.00')"
    out1 = sb.execute(code)
    out2 = sb.execute(code)
    ok = out1.result_id != out2.result_id
    return _run("33", ok)


def test_rule_ids_extraidos():
    """rule_ids_applied incluye el rule_id de calc_ivu."""
    sb = CalculationSandbox()
    code = "result = calc_ivu(Decimal('1000.00'), None, date(2025, 6, 1), True)"
    out = sb.execute(code)
    ok = out.success and len(out.rule_ids_applied) > 0
    return _run("34", ok, f"rule_ids={out.rule_ids_applied}")


def test_sandbox_result_frozen():
    """SandboxResult es inmutable (frozen Pydantic)."""
    sb = CalculationSandbox()
    code = "result = Decimal('42.00')"
    out = sb.execute(code)
    try:
        out.success = False  # type: ignore[misc]
        ok = False
    except Exception:
        ok = True
    return _run("35", ok)


def test_parametros_inyectados():
    """Variables en parameters son accesibles en el codigo."""
    sb = CalculationSandbox()
    code = """
r = calc_ivu(monto_base, None, fecha_calculo, True)
result = r
"""
    params = {
        "monto_base":    Decimal("2500.00"),
        "fecha_calculo": date(2025, 9, 1),
    }
    out = sb.execute(code, parameters=params)
    ok = out.success and out.result_value["total_ivu"] == "287.50"
    return _run("36", ok, f"error={out.error} val={out.result_value}")


def test_error_exacto_sin_parciales():
    """Si el codigo falla a mitad, no hay resultados parciales."""
    sb = CalculationSandbox()
    code = """
r1 = calc_ivu(Decimal("1000.00"), None, date(2025, 1, 1), True)
r2 = calc_ivu(Decimal("-999.00"), None, date(2025, 1, 1), True)  # falla aqui
result = r2
"""
    out = sb.execute(code)
    ok = not out.success and out.result_value is None
    return _run("37", ok, f"error={out.error}")


# ===========================================================================
# RUNNER
# ===========================================================================

ALL_TESTS = [
    test_ivu_basico,
    test_ivu_solo_estatal,
    test_ivu_monto_cero,
    test_ivu_monto_negativo_rechazado,
    test_ivu_exento,
    test_fica_ss_normal,
    test_fica_medicare_normal,
    test_fica_ss_sobre_tope,
    test_fica_ss_tope_parcial,
    test_fica_medicare_adicional,
    test_futa_neta_sobre_primer_7000,
    test_futa_tope_aplicado,
    test_futa_tope_parcial,
    test_retencion_honorarios,
    test_retencion_tipo_invalido,
    test_retencion_monto_cero,
    test_depreciacion_linea_recta,
    test_depreciacion_doble_saldo,
    test_depreciacion_costo_cero,
    test_depreciacion_metodo_invalido,
    test_import_prohibido_os,
    test_import_prohibido_requests,
    test_eval_prohibido,
    test_exec_prohibido,
    test_open_prohibido,
    test_while_true_sin_break,
    test_acceso_dunder_prohibido,
    test_builtins_globals_prohibido,
    test_import_from_math_permitido,
    test_codigo_sin_result,
    test_calc_hash_presente,
    test_calc_hash_unico_por_ejecucion,
    test_result_id_unico,
    test_rule_ids_extraidos,
    test_sandbox_result_frozen,
    test_parametros_inyectados,
    test_error_exacto_sin_parciales,
]

if __name__ == "__main__":
    print("=" * 70)
    print("TESTS: Calculation Sandbox — Bit-Counting PR")
    print("=" * 70)

    passed = 0
    failed = 0
    errors = []

    for test_fn in ALL_TESTS:
        try:
            ok, msg = test_fn()
            if ok:
                passed += 1
            else:
                failed += 1
                errors.append(msg)
        except Exception as exc:
            failed += 1
            label = test_fn.__name__
            errors.append(f"  EXCEPTION en {label}: {exc}")
            print(f"  EXCEPTION: {label}: {exc}")

    print("=" * 70)
    print(f"RESULTADO: {passed} passed, {failed} failed")
    if errors:
        print("\nFALLIDOS:")
        for e in errors:
            print(e)
    else:
        print("TODOS LOS TESTS PASARON.")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)
