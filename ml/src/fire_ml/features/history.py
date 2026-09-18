"""T023 — 이력 파생. EDA §6-3 ② — 이력 피처는 반드시 '율'로.

`전년화재건수` 는 인구 규모를 다시 끌고 들어와 화재율과 음의 상관까지 나온다.
`전년화재율` 을 쓰고, offset(log_인구) 과 함께만 쓴다.
2021년 행은 전년(2020) 자료가 없어 null 이므로 플래그를 함께 넣는다.
"""
from __future__ import annotations

import pandas as pd


def add(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "전년화재율" in out.columns:
        out["전년자료있음"] = out["전년화재율"].notna().astype(int)
    else:
        out["전년화재율"] = pd.NA
        out["전년자료있음"] = 0
    # 인구 규모를 재유입시키므로 학습에 쓰지 않는다
    out = out.drop(columns=[c for c in ["전년화재건수"] if c in out.columns])
    return out
