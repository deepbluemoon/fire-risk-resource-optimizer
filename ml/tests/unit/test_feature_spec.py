"""T012 — SC-016 자동 검증. C등급(사후한정) 피처가 어떤 모델 입력에도 없어야 한다."""
import pytest

from fire_ml.features.spec import (GRADES, MODEL_FEATURES, Grade, LeakageError,
                                   feature_grade_map, forecast_dependent, get_features, grade_of)

POST_HOC = ["발화요인", "최초착화물대분류", "진화시간_분", "피해액_천원", "사망", "부상",
            "연소확대", "원인", "시각", "장소중분류", "온도", "풍향", "풍속구간"]


@pytest.mark.parametrize("model_id", list(MODEL_FEATURES))
def test_no_post_hoc_features(model_id):
    feats = get_features(model_id)
    leaked = [f for f in feats if f in POST_HOC]
    assert leaked == [], f"{model_id} 에 사후 조사 항목이 새어 들어갔다: {leaked}"


@pytest.mark.parametrize("model_id", list(MODEL_FEATURES))
def test_all_features_registered(model_id):
    """FR-043 — 관측가능성이 문서화되지 않은 변수는 학습에 사용할 수 없다."""
    for f in get_features(model_id):
        assert grade_of(f) in (Grade.A, Grade.B)


def test_unregistered_feature_raises():
    with pytest.raises(KeyError):
        grade_of("존재하지않는피처")


def test_leakage_detected():
    MODEL_FEATURES["_probe"] = ["실효습도", "발화요인"]
    try:
        with pytest.raises(LeakageError):
            get_features("_probe")
    finally:
        del MODEL_FEATURES["_probe"]


def test_m2a_excludes_event_only_features():
    """plan.md §M-4 — 일 단위 재정의로 시각·장소중분류·발화요인이 구조적으로 사라진다."""
    feats = get_features("M2a")
    for f in ("시각", "장소중분류", "발화요인", "온도", "풍향", "풍속구간"):
        assert f not in feats


def test_forecast_dependent_listed():
    b = forecast_dependent("M1")
    assert "기온" in b and "일교차" in b and "풍속" in b
    assert "실효습도" not in b, "실효습도는 과거 6일 관측이 0.70 을 확정하므로 A등급"


def test_grade_map_serializable():
    m = feature_grade_map("M1")
    assert set(m.values()) <= {"A", "B"}
    assert len(m) == len(get_features("M1"))
