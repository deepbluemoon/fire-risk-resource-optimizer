"""T022 — F-INT 상호작용 블록 (plan.md §D-5, EDA §6-3 ④).

상관계수는 직선 관계만 잡는다. EDA §6-5·6-6 반응면에서 단독 항으로는 안 잡히는
조합이 확인됐다 — 건조 × 바람, 무강수 × 기온.

트리는 상호작용을 스스로 만들 수 있으나 개발셋이 24개월뿐이라 과적합 위험이 크고,
GLM(M1-GLM)에는 명시 항이 필수다. 두 모델이 이 블록을 공유한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NAMES = ["풍속건조지수", "건조지속강도", "한랭건조", "임야건조"]


def _col(df: pd.DataFrame, *cands: str) -> pd.Series:
    """데이터셋마다 컬럼명이 다르다 (발생=기온/풍속, 확산=일기온/일풍속)."""
    for c in cands:
        if c in df.columns:
            return pd.to_numeric(df[c], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def add(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    eh = _col(df, "실효습도").clip(0, 100)
    dry = (100.0 - eh) / 100.0                       # 건조도 0~1
    wind = _col(df, "풍속", "일풍속")
    nodrain = _col(df, "무강수일수").clip(lower=0)
    temp = _col(df, "기온", "일기온")
    forest = _col(df, "산림율")

    out["풍속건조지수"] = wind * dry                  # 건조 × 바람
    out["건조지속강도"] = np.sqrt(nodrain) * dry       # 무강수 지속 × 건조
    out["한랭건조"] = (1.0 - temp / 30.0).clip(-1, 2) * dry   # 겨울 난방기 조건
    out["임야건조"] = (forest / 100.0) * dry           # 임야 연소확대율 55.0% (전 유형 최고)
    return out
