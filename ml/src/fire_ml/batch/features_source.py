"""배치 피처 공급원.

운영 경로는 기상청 단기예보(`fetch_forecast.py`, C-1 키 필요)다.
그 전까지는 과거 관측(학습 데이터셋)을 공급원으로 쓴다 — 2021~2023 구간에서만 가능하며,
그 밖의 날짜는 기상 입력이 없으므로 M0 기저율로 폴백한다 (FR-024 · SC-004).

관측과 예보는 다른 값이다. 이 경로로 만든 예측은 `confidence='정상'` 이지만
`source='관측'` 으로 남겨 나중에 예보 기반 결과와 구분할 수 있게 한다.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache

import pandas as pd

from ..data.loaders import load_raw
from ..data.regions import region_cd


@lru_cache(maxsize=1)
def _panel() -> pd.DataFrame:
    df = load_raw("occurrence")
    df["region_cd"] = region_cd(df["시도"], df["시군구"])
    return df


def available_range() -> tuple[date, date]:
    p = _panel()
    return p["날짜"].min().date(), p["날짜"].max().date()


def for_date(target: date) -> pd.DataFrame | None:
    """해당 일자의 251행 기상·지역 피처. 없으면 None (→ 폴백)."""
    p = _panel()
    d = p[p["날짜"] == pd.Timestamp(target)]
    return d.reset_index(drop=True) if len(d) else None
