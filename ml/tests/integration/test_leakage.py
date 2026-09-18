"""T114 — SC-016 최종 확인.

레지스트리에 적재된 **실제 아티팩트의 피처 목록**을 역검증한다.
코드가 아니라 DB 에 남은 기록을 본다 — 학습 시점에 무엇이 들어갔는지가 진실이다.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pymysql")

try:
    from fire_ml.data.db import query
    query("SELECT 1 AS ok")
except Exception as e:  # pragma: no cover
    pytest.skip(f"DB 접속 불가: {e}", allow_module_level=True)

from fire_ml.features.spec import GRADES, Grade

POST_HOC = {f for f, g in GRADES.items() if g is Grade.C}


@pytest.fixture(scope="module")
def registry():
    rows = query("""SELECT model_id, model_version, feature_list, acceptance_passed, is_active
                    FROM ops_model_registry""")
    assert rows, "적재된 모델이 없다"
    return rows


def test_no_post_hoc_feature_in_any_artifact(registry):
    for r in registry:
        fl = r["feature_list"]
        feats = json.loads(fl) if isinstance(fl, str) else (fl or [])
        names = {f["name"] for f in feats if isinstance(f, dict)}
        leaked = names & POST_HOC
        assert not leaked, f"{r['model_id']}({r['model_version']}) 에 사후 조사 항목: {leaked}"


def test_every_feature_has_documented_grade(registry):
    """FR-043 — 관측가능성이 문서화되지 않은 변수는 학습에 쓸 수 없다."""
    for r in registry:
        fl = r["feature_list"]
        feats = json.loads(fl) if isinstance(fl, str) else (fl or [])
        for f in feats:
            assert f.get("grade") in ("A", "B"), \
                f"{r['model_id']} 의 {f.get('name')} 등급이 {f.get('grade')}"


def test_snapshot_grades_match_registry():
    """저장된 예측 스냅샷의 등급도 A/B 뿐이어야 한다."""
    rows = query("""SELECT feature_grade FROM pred_feature_snapshot
                    ORDER BY target_date DESC LIMIT 50""")
    assert rows
    for r in rows:
        g = r["feature_grade"]
        grades = set((json.loads(g) if isinstance(g, str) else g).values())
        assert grades <= {"A", "B"}


def test_failed_models_are_marked(registry):
    """plan.md §M-9 — 미통과 모델은 그 사실이 기록되어야 한다.

    M3 는 top-3 가 상수 예측기와 동률이라 합격하지 못했다. 화면이 이를 숨기면
    사용자가 순위를 근거로 대응 장비를 고르게 된다.
    """
    by_id = {r["model_id"]: r for r in registry}
    if "M3" in by_id:
        assert by_id["M3"]["acceptance_passed"] == 0, \
            "M3 는 홀드아웃에서 상수 베이스라인을 넘지 못했다 — 합격으로 표시하면 안 된다"
