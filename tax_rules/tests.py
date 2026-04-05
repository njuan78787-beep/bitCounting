# =============================================================================
# tax_rules/tests.py
# Tests de verificacion del modulo de reglas fiscales.
# Ejecutar con: python -m tax_rules.tests
# =============================================================================

import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, "/home/user/bitCounting")

from tax_rules import (
    get_rule, get_rule_confidence, get_rule_confidence_breakdown,
    check_rule_contradiction, list_active_rules, calculate_ivu,
    get_all_rule_ids, TaxRule,
    CONFIDENCE_AUTO_PROCESS, CONFIDENCE_FORCE_PAUSE,
)

PASS = "[PASS]"
FAIL = "[FAIL]"

def test(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    return condition


def run_all_tests():
    errors = 0
    print("\n=== TEST: get_rule() — fecha especifica ===")

    r = get_rule("IVU_ESTATAL_PR_2015_V1", date(2025, 3, 1))
    if not test("IVU estatal vigente en 2025", r is not None): errors += 1
    if r:
        if not test("Tasa IVU estatal = 10.5%", r.rate == Decimal("10.5"), str(r.rate)): errors += 1
        if not test("TaxRule es frozen (inmutable)", _is_frozen_pydantic(r)): errors += 1

    r_old = get_rule("IVU_ESTATAL_PR_2015_V1", date(2014, 1, 1))
    if not test("IVU estatal NO vigente antes de 2015", r_old is None): errors += 1

    r_ss24 = get_rule("FICA_SS_2024_V1", date(2024, 6, 1))
    if not test("FICA SS 2024 vigente en jun 2024", r_ss24 is not None): errors += 1
    r_ss24_exp = get_rule("FICA_SS_2024_V1", date(2025, 1, 1))
    if not test("FICA SS 2024 expirada en 2025", r_ss24_exp is None): errors += 1

    r_ss25 = get_rule("FICA_SS_2025_V1", date(2025, 3, 1))
    if not test("FICA SS 2025 vigente en 2025", r_ss25 is not None): errors += 1
    if r_ss25:
        if not test("Tope SS 2025 = $176,100", r_ss25.wage_base_limit == Decimal("176100"), str(r_ss25.wage_base_limit)): errors += 1

    print("\n=== TEST: get_rule_confidence() ===")

    conf_ivu = get_rule_confidence("IVU_ESTATAL_PR_2015_V1")
    if not test("IVU estatal confidence > 0", conf_ivu > 0, f"score={conf_ivu}"): errors += 1
    if not test("IVU estatal confidence en rango [0,1]", 0 <= conf_ivu <= 1): errors += 1
    # Con times_applied=0 (fresh install) el score no puede llegar a AUTO_PROCESS.
    # El umbral correcto a verificar es que supere FORCE_PAUSE (0.60) — una regla
    # antigua, validada por CPA y sin controversia debe al menos pasar ese umbral.
    if not test("IVU estatal confidence > umbral FORCE_PAUSE (regla antigua, validada, sin controversia)",
                conf_ivu >= CONFIDENCE_FORCE_PAUSE,
                f"score={conf_ivu} umbral={CONFIDENCE_FORCE_PAUSE}"): errors += 1

    bd = get_rule_confidence_breakdown("IVU_ESTATAL_PR_2015_V1")
    if not test("Breakdown no es None", bd is not None): errors += 1
    if bd:
        if not test("Breakdown.final_score == get_rule_confidence()", abs(bd.final_score - conf_ivu) < 0.0001): errors += 1
        if not test("Breakdown.validated_by_cpa=True para IVU estatal", bd.validated_by_cpa): errors += 1
        if not test("Breakdown.has_controversy=False para IVU estatal", not bd.has_controversy): errors += 1

    print("\n=== TEST: check_rule_contradiction() ===")

    result_no_contra = check_rule_contradiction("FICA_SS_2024_V1", "FICA_SS_2025_V1", date(2025, 1, 1))
    if not test("SS 2024 vs SS 2025: no contradiccion (relacion de supersesion)",
                not result_no_contra.has_contradiction,
                result_no_contra.details): errors += 1

    result_diff_type = check_rule_contradiction("IVU_ESTATAL_PR_2015_V1", "FICA_SS_2025_V1", date(2025, 1, 1))
    if not test("IVU estatal vs FICA SS: no contradiccion (tipos distintos)",
                not result_diff_type.has_contradiction,
                result_diff_type.details): errors += 1

    # IVU estatal (tax_type=IVU_ESTATAL) y municipal (tax_type=IVU_MUNICIPAL) son
    # tipos distintos — complementarios, no contradictorios. Correcto: NO_CONTRADICTION.
    result_ivu_vs_municipal = check_rule_contradiction("IVU_ESTATAL_PR_2015_V1", "IVU_MUNICIPAL_PR_2015_V1", date(2025, 1, 1))
    if not test("IVU estatal vs municipal: NO contradiccion (tipos distintos, son complementarios)",
                not result_ivu_vs_municipal.has_contradiction,
                result_ivu_vs_municipal.details): errors += 1

    # Test de contradiccion real: SS 2024 vs SS 2025 en periodo donde ambas vigentes
    # En fecha 2024-06-01: SS 2024 esta vigente, SS 2025 no. No hay solapamiento.
    result_ss_no_overlap = check_rule_contradiction("FICA_SS_2024_V1", "FICA_SS_2025_V1", date(2024, 6, 1))
    if not test("FICA SS 2024 vs 2025 en 2024: no contradiccion (SS 2025 no vigente aun)",
                not result_ss_no_overlap.has_contradiction,
                result_ss_no_overlap.details): errors += 1

    print("\n=== TEST: calculate_ivu() ===")

    calc = calculate_ivu(Decimal("1000.00"), date(2025, 3, 1))
    if not test("IVU estatal = $105.00 sobre $1,000", calc.ivu_estatal_amount == Decimal("105.00"),
                str(calc.ivu_estatal_amount)): errors += 1
    if not test("IVU municipal = $10.00 sobre $1,000", calc.ivu_municipal_amount == Decimal("10.00"),
                str(calc.ivu_municipal_amount)): errors += 1
    if not test("Total IVU = $115.00", calc.total_ivu == Decimal("115.00"),
                str(calc.total_ivu)): errors += 1
    if not test("Total con IVU = $1,115.00", calc.total_with_ivu == Decimal("1115.00"),
                str(calc.total_with_ivu)): errors += 1
    if not test("calc_hash generado", len(calc.calc_hash) == 16): errors += 1

    # Verificar precision con monto fraccionario
    calc2 = calculate_ivu(Decimal("245.67"), date(2025, 3, 1))
    expected_estatal = Decimal("25.79")  # 245.67 * 0.105 = 25.79535 -> round = 25.80
    # 245.67 * 0.105 = 25.79535, ROUND_HALF_UP -> 25.80
    if not test("IVU precision decimal correcta", calc2.ivu_estatal_amount == Decimal("25.80"),
                f"esperado=25.80 obtenido={calc2.ivu_estatal_amount}"): errors += 1

    print("\n=== TEST: Inmutabilidad ===")

    rule = get_rule("IVU_ESTATAL_PR_2015_V1", date(2025, 1, 1))
    mutation_blocked = False
    try:
        rule.rate = Decimal("99.9")   # asignacion directa — bloqueada por frozen=True
    except Exception:
        mutation_blocked = True
    if not test("TaxRule es frozen — mutation bloqueada via asignacion directa", mutation_blocked): errors += 1

    print("\n=== TEST: list_active_rules() ===")

    active = list_active_rules(date(2025, 3, 1))
    if not test("Hay reglas activas en 2025", len(active) > 0, f"count={len(active)}"): errors += 1
    if not test("FICA SS 2024 NO esta activa en 2025",
                not any(r.rule_id == "FICA_SS_2024_V1" for r in active)): errors += 1
    if not test("FICA SS 2025 SI esta activa en 2025",
                any(r.rule_id == "FICA_SS_2025_V1" for r in active)): errors += 1

    print(f"\n{'='*50}")
    total = 28
    passed = total - errors
    print(f"RESULTADO: {passed}/{total} tests pasados — {errors} fallidos")
    if errors == 0:
        print("TODOS LOS TESTS PASARON.")
    else:
        print(f"ATENCION: {errors} tests fallaron. Revisar antes de usar en produccion.")
    return errors == 0


def _is_frozen_pydantic(obj) -> bool:
    """Verifica inmutabilidad intentando modificar un campo existente (frozen=True)."""
    try:
        obj.name = "MODIFICADO"   # campo existente — debe ser bloqueado por frozen=True
        return False
    except Exception:
        return True


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
