"""피처 관측가능성 레지스트리 (plan.md §D-3).

모든 모델은 이 모듈을 통해서만 피처 목록을 얻는다.

등급
  A 과거확정  — 예측일 이전 관측으로 확정. 그대로 사용
  B 예보의존  — 예측일 당일 값. 운영에서는 예보값으로 대체된다
  C 사후한정  — 사건 후에만 확정. **입력 금지** (타깃으로만 허용)

C 등급이 입력 목록에 새어 들어가면 FR-040~043 · SC-016 위반이며,
검증 성능만 비정상적으로 높아지고 실제 운영에서 무너진다.
"""
from __future__ import annotations

from enum import Enum


class Grade(str, Enum):
    A = "A"  # 과거확정
    B = "B"  # 예보의존
    C = "C"  # 사후한정 — 입력 금지


GRADES: dict[str, Grade] = {
    # --- A. 과거확정 -------------------------------------------------------
    "실효습도": Grade.A,
    "무강수일수": Grade.A,
    "전년화재율": Grade.A,
    "전년자료있음": Grade.A,
    "산림율": Grade.A,
    "인구밀도": Grade.A,
    "평균소방서거리": Grade.A,
    "소방서거리": Grade.A,
    "log_인구": Grade.A,
    "지역유형": Grade.A,
    "기상배정방식": Grade.A,
    "연중일": Grade.A,
    "월": Grade.A,
    "요일": Grade.A,
    "주말": Grade.A,
    "계절": Grade.A,
    # F-INT 상호작용 블록 (plan.md §D-5) — B 성분을 포함하므로 파생 등급은 B
    # --- B. 예보의존 -------------------------------------------------------
    "기온": Grade.B,
    "최저기온": Grade.B,
    "최고기온": Grade.B,
    "일교차": Grade.B,
    "풍속": Grade.B,
    "강수량": Grade.B,
    "최소습도": Grade.B,
    "습도": Grade.B,
    "일기온": Grade.B,
    "일습도": Grade.B,
    "일최소습도": Grade.B,
    "일풍속": Grade.B,
    "풍속건조지수": Grade.B,
    "건조지속강도": Grade.B,
    "한랭건조": Grade.B,
    "임야건조": Grade.B,
    # --- C. 사후한정 (입력 금지) -------------------------------------------
    "발화요인": Grade.C,
    "원인": Grade.C,
    "최초착화물대분류": Grade.C,
    "진화시간": Grade.C,
    "진화시간_분": Grade.C,
    "피해액_천원": Grade.C,
    "사망": Grade.C,
    "부상": Grade.C,
    "인명피해": Grade.C,
    "연소확대": Grade.C,
    "화재유형": Grade.C,
    "온도": Grade.C,      # 화재 건에 부기된 현장 기록 — 사건 후에만 존재
    "풍향": Grade.C,
    "풍속구간": Grade.C,
    "시각": Grade.C,
    "장소중분류": Grade.C,
}

# 모델별 입력 피처 (plan.md §M-2 ~ §M-7)
MODEL_FEATURES: dict[str, list[str]] = {
    "M1": [
        "실효습도", "최소습도", "무강수일수", "강수량", "일교차", "기온", "풍속",
        "연중일", "요일", "주말",
        "산림율", "인구밀도", "평균소방서거리", "지역유형",
        "전년화재율", "전년자료있음", "기상배정방식",
        "풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
    ],
    "M5": [  # 발생 확률 — M1 과 동일 세트
        "실효습도", "최소습도", "무강수일수", "강수량", "일교차", "기온", "풍속",
        "연중일", "요일", "주말",
        "산림율", "인구밀도", "평균소방서거리", "지역유형",
        "전년화재율", "전년자료있음", "기상배정방식",
        "풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
    ],
    "M2a": [  # 일 단위 재정의 (plan.md §M-4) — 시각·장소중분류·발화요인 제외
        # `일교차` 는 spread·cause 원자료에 없다 — 학습할 수 없는 변수는 넣지 않는다 (FR-043)
        "실효습도", "일최소습도", "무강수일수", "강수량", "일기온", "일풍속",
        "월", "요일", "주말",
        "산림율", "인구밀도", "소방서거리", "지역유형",
        "풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
    ],
    "M3": [
        # `일교차` 는 spread·cause 원자료에 없다 — 학습할 수 없는 변수는 넣지 않는다 (FR-043)
        "실효습도", "일최소습도", "무강수일수", "강수량", "일기온", "일풍속",
        "월", "요일", "주말",
        "산림율", "인구밀도", "소방서거리", "지역유형",
        "풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
    ],
    "M4": [
        # `일교차` 는 spread·cause 원자료에 없다 — 학습할 수 없는 변수는 넣지 않는다 (FR-043)
        "실효습도", "일최소습도", "무강수일수", "강수량", "일기온", "일풍속",
        "월", "요일", "주말",
        "산림율", "인구밀도", "소방서거리", "지역유형",
        "풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
    ],
    "M0": [],  # 룩업 — 지역·계절만 사용
}

TARGETS: dict[str, str] = {
    "M0": "화재건수", "M1": "화재건수", "M5": "화재발생",
    "M2a": "연소확대", "M3": "원인", "M4": "장기진화",
}


class LeakageError(RuntimeError):
    """C등급(사후한정) 피처가 입력에 포함되었다."""


def grade_of(feature: str) -> Grade:
    if feature not in GRADES:
        raise KeyError(
            f"미등록 피처 '{feature}'. FR-043 에 따라 관측가능성이 문서화되지 않은 변수는 "
            f"학습에 사용할 수 없다. GRADES 에 등급을 등록하라."
        )
    return GRADES[feature]


def get_features(model_id: str, mode: str = "train") -> list[str]:
    """모델의 입력 피처 목록. C등급이 섞여 있으면 LeakageError 를 던진다."""
    if model_id not in MODEL_FEATURES:
        raise KeyError(f"미등록 모델 '{model_id}'")
    feats = MODEL_FEATURES[model_id]
    leaked = [f for f in feats if grade_of(f) is Grade.C]
    if leaked:
        raise LeakageError(
            f"{model_id} 입력에 사후한정(C등급) 피처가 포함되었다: {leaked}. "
            f"FR-040~043 · SC-016 위반이다."
        )
    if mode not in ("train", "serve"):
        raise ValueError("mode 는 'train' 또는 'serve'")
    return list(feats)


def forecast_dependent(model_id: str) -> list[str]:
    """운영에서 예보값으로 대체해야 하는 B등급 피처."""
    return [f for f in get_features(model_id) if grade_of(f) is Grade.B]


def feature_grade_map(model_id: str) -> dict[str, str]:
    """pred_feature_snapshot.feature_grade 에 저장할 등급 맵."""
    return {f: grade_of(f).value for f in get_features(model_id)}
