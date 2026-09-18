"""지역 마스터 — 251 시군구의 안정적인 키와 정적 프로파일."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .loaders import load_raw

PROFILE_COLS = ["인구", "세대수", "면적_km2", "산림율", "임목축적", "인구밀도",
                "평균소방서거리", "log_인구"]


def region_cd(sido: pd.Series, sigungu: pd.Series) -> pd.Series:
    """시도|시군구 → 안정적 문자열 키. 같은 시군구명이 여러 시도에 존재한다."""
    return sido.astype(str).str.strip() + "|" + sigungu.astype(str).str.strip()


def region_master() -> pd.DataFrame:
    """251행 정적 프로파일. 시간에 거의 변하지 않는 값만 남긴다."""
    df = load_raw("occurrence")
    df["region_cd"] = region_cd(df["시도"], df["시군구"])
    agg = {c: "median" for c in PROFILE_COLS if c in df.columns}
    m = (df.groupby(["region_cd", "시도", "시군구"], as_index=False)
           .agg({**agg, "기상배정방식": "first", "기상산악지점": "first"}))
    m = m.rename(columns={"시도": "sido", "시군구": "sigungu"})
    assert len(m) == 251, f"지역 마스터가 251행이 아니다: {len(m)}"
    return m


def actual_daily() -> pd.DataFrame:
    """일자 × 지역 실제 발생 건수 (2021~2023)."""
    df = load_raw("occurrence")
    df["region_cd"] = region_cd(df["시도"], df["시군구"])
    return df[["날짜", "region_cd", "화재건수"]].rename(
        columns={"날짜": "target_date", "화재건수": "fire_count"})
