"""T052 — 일일 배치 종단 검증. DB 접속이 필요하므로 실패 시 skip 한다."""
from __future__ import annotations

import json
from datetime import date

import pytest

pytest.importorskip("pymysql")

try:
    from fire_ml.data.db import query
    query("SELECT 1 AS ok")
except Exception as e:  # pragma: no cover
    pytest.skip(f"DB 접속 불가: {e}", allow_module_level=True)


@pytest.fixture(scope="module")
def latest():
    r = query("SELECT MAX(target_date) d FROM pred_daily WHERE is_current=1")
    assert r and r[0]["d"], "pred_daily 에 예측이 없다 — 배치를 먼저 실행하라"
    return r[0]["d"]


def test_exactly_251_rows_per_day(latest):
    """SC-006 — 예측 불가 지역도 행을 남긴다. 목록에서 누락되지 않는다."""
    n = query("SELECT COUNT(*) c FROM pred_daily WHERE target_date=%s AND is_current=1", (latest,))[0]["c"]
    assert n == 251, f"{latest} 예측이 {n}행 (251 기대)"


def test_every_row_has_settled_status(latest):
    rows = query("""SELECT risk_grade, data_status FROM pred_daily
                    WHERE target_date=%s AND is_current=1""", (latest,))
    for r in rows:
        assert r["risk_grade"] is not None or r["data_status"] == "데이터없음"


def test_is_current_is_unique_per_cell(latest):
    """is_current 전환은 단일 트랜잭션 — 조회 중 0행/2행이 나오면 안 된다."""
    dup = query("""SELECT target_date, region_cd, COUNT(*) c FROM pred_daily
                   WHERE is_current=1 GROUP BY target_date, region_cd HAVING c<>1 LIMIT 5""")
    assert dup == [], f"is_current 가 중복/누락된 셀: {dup}"


def test_grades_derive_from_reference_bounds(latest):
    """등급은 ref_risk_grade 경계에서만 유도된다 — 직접 쓰지 않는다.

    경계는 **모델 버전마다 다르다** (M0 폴백과 M1 은 확률 분포가 다르므로 분위수도 다르다).
    따라서 그 행을 실제로 만든 버전의 경계로 검증해야 한다.
    """
    bounds: dict[tuple[str, str], tuple[float, float]] = {
        (b["model_version"], b["grade"]): (float(b["prob_lower"]), float(b["prob_upper"]))
        for b in query("SELECT model_version, grade, prob_lower, prob_upper FROM ref_risk_grade")
    }
    rows = query("""SELECT risk_grade, occur_probability p, source_model_version v
                    FROM pred_daily
                    WHERE target_date=%s AND is_current=1 AND risk_grade IS NOT NULL""", (latest,))
    assert rows
    for r in rows:
        key = (r["v"], r["risk_grade"])
        assert key in bounds, f"{r['v']} 버전의 등급 경계가 ref_risk_grade 에 없다"
        lo, hi = bounds[key]
        assert lo <= float(r["p"]) <= hi + 1e-9, f"{r['v']} {r['risk_grade']} 경계 밖: {r['p']}"


def test_every_used_model_version_has_grade_bounds():
    """등급을 만든 모든 모델 버전에 경계가 등록되어 있어야 한다."""
    used = {r["v"] for r in query("""SELECT DISTINCT source_model_version v FROM pred_daily
                                     WHERE is_current=1 AND risk_grade IS NOT NULL""")}
    have = {r["v"] for r in query("SELECT DISTINCT model_version v FROM ref_risk_grade")}
    assert used <= have, f"경계가 없는 모델 버전: {used - have}"


def test_feature_snapshot_has_no_post_hoc_grade(latest):
    """SC-016 — 저장된 입력 등급에 C(사후한정)가 없어야 한다."""
    rows = query("""SELECT feature_grade FROM pred_feature_snapshot
                    WHERE target_date=%s LIMIT 20""", (latest,))
    assert rows, "입력 스냅샷이 없다 — SC-005 재구성 불가"
    for r in rows:
        g = r["feature_grade"]
        grades = set((json.loads(g) if isinstance(g, str) else g).values())
        assert grades <= {"A", "B"}, f"사후한정 등급이 저장되었다: {grades}"


def test_batch_run_recorded(latest):
    """FR-025 — 실패를 조용히 넘기지 않는다."""
    r = query("""SELECT status, regions_ok, regions_failed FROM ops_batch_run
                 WHERE target_date=%s ORDER BY run_id DESC LIMIT 1""", (latest,))
    assert r, "배치 실행 기록이 없다"
    assert r[0]["regions_ok"] + r[0]["regions_failed"] == 251
