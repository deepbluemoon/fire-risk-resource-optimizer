"""모델 입력 프레임 조립 — 레지스트리(features/spec.py)가 요구하는 열만 만들어 넘긴다."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.regions import region_cd
from . import history, interactions
from .spec import get_features

CAT_COLS = {"기상배정방식", "지역유형", "요일"}

# 같은 물리량인데 데이터셋마다 이름이 다르다.
#   발생 패널(occurrence): 시군구×일 단위        → 기온 · 최소습도 · 풍속 · 평균소방서거리
#   확산/원인(spread·cause): 화재 1건 단위        → 일기온 · 일최소습도 · 일풍속 · 소방서거리
# 별칭을 두지 않으면 학습에는 있고 추론에는 없는 열이 생겨 트리가 결측 분기로 새어 나간다.
ALIASES: dict[str, tuple[str, ...]] = {
    "일기온": ("기온",),
    "일최소습도": ("최소습도",),
    "일풍속": ("풍속",),
    "일습도": ("습도",),
    "소방서거리": ("평균소방서거리",),
    "평균소방서거리": ("소방서거리",),
    "기온": ("일기온",),
    "최소습도": ("일최소습도",),
    "풍속": ("일풍속",),
}


def build(df: pd.DataFrame, model_id: str, clusters: pd.DataFrame | None = None) -> pd.DataFrame:
    """원본 데이터셋 → 모델 입력 프레임. 누락 열은 만들고, C등급은 레지스트리가 막는다."""
    out = df.copy()
    out["region_cd"] = region_cd(out["시도"], out["시군구"])

    if clusters is not None:
        m = dict(zip(clusters["region_cd"], clusters["cluster_id"]))
        out["지역유형"] = out["region_cd"].map(m).fillna(-1).astype(int)

    if "월" not in out.columns and "날짜" in out.columns:
        out["월"] = pd.to_datetime(out["날짜"]).dt.month
    if "연중일" not in out.columns and "날짜" in out.columns:
        out["연중일"] = pd.to_datetime(out["날짜"]).dt.dayofyear

    out = history.add(out)

    feats = get_features(model_id)
    for f in feats:
        if f in out.columns:
            continue
        for alt in ALIASES.get(f, ()):
            if alt in out.columns:
                out[f] = out[alt]
                break
        else:
            out[f] = np.nan

    out = interactions.add(out)      # 별칭 해소 후에 파생한다
    for f in feats:
        if f not in out.columns:
            out[f] = np.nan

    X = out[feats].copy()
    for c in X.columns:
        if c in CAT_COLS:
            X[c] = X[c].astype("category")
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X


def offset_log_population(df: pd.DataFrame) -> np.ndarray:
    """Poisson offset. log_인구 가 없으면 인구에서 만든다."""
    if "log_인구" in df.columns:
        v = pd.to_numeric(df["log_인구"], errors="coerce")
    else:
        v = np.log1p(pd.to_numeric(df["인구"], errors="coerce"))
    return v.fillna(v.median()).to_numpy(dtype=float)
