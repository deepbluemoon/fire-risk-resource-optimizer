"""M0 — 기저율 룩업 (plan.md §M-1).

지역평균(스무딩 m=20) × 전역 계절계수. 파라미터 255개.
계절성은 '쪼개기'가 아니라 '곱셈 보정'으로 넣는다 — 지역×월 셀(3,012 파라미터)은
train 에서 가장 좋고 holdout 에서 가장 나쁘다(교과서적 과적합).

이 모델이 서비스의 하한선이자 M1 의 심판이며, 배치 실패 시 폴백이다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REGION_KEY = ["시도", "시군구"]
SMOOTHING_M = 20.0

_SEASON = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
           6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}


def season_of(month) -> pd.Series | str:
    if np.isscalar(month):
        return _SEASON[int(month)]
    return pd.Series(month).map(_SEASON)


def region_id(df: pd.DataFrame) -> pd.Series:
    return df["시도"].astype(str) + "|" + df["시군구"].astype(str)


class M0Lookup:
    """지역평균 × 전역 계절계수(또는 월계수) 룩업."""

    def __init__(self, season_mode: str = "season", smoothing: float = SMOOTHING_M):
        if season_mode not in ("none", "season", "month"):
            raise ValueError("season_mode 는 'none' | 'season' | 'month'")
        self.season_mode = season_mode
        self.smoothing = smoothing
        self.global_mean_: float | None = None
        self.region_mean_: dict[str, float] = {}
        self.season_factor_: dict[str, float] = {}

    def fit(self, df: pd.DataFrame, target: str = "화재건수") -> "M0Lookup":
        y = df[target].astype(float)
        self.global_mean_ = float(y.mean())

        g = df.assign(_rid=region_id(df), _y=y).groupby("_rid")["_y"]
        s, n = g.sum(), g.size()
        sm = (s + self.smoothing * self.global_mean_) / (n + self.smoothing)
        self.region_mean_ = sm.to_dict()

        if self.season_mode != "none":
            key = season_of(df["날짜"].dt.month) if self.season_mode == "season" \
                else df["날짜"].dt.month.astype(str)
            fac = df.assign(_k=list(key), _y=y).groupby("_k")["_y"].mean() / self.global_mean_
            self.season_factor_ = {str(k): float(v) for k, v in fac.items()}
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        base = region_id(df).map(self.region_mean_).fillna(self.global_mean_).to_numpy(dtype=float)
        if self.season_mode == "none":
            return base
        key = season_of(df["날짜"].dt.month) if self.season_mode == "season" \
            else df["날짜"].dt.month.astype(str)
        f = pd.Series(list(key), index=df.index).map(self.season_factor_).fillna(1.0).to_numpy(float)
        return base * f

    # --- 직렬화 --------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "model_id": "M0",
            "season_mode": self.season_mode,
            "smoothing": self.smoothing,
            "global_mean": self.global_mean_,
            "region_mean": self.region_mean_,
            "season_factor": self.season_factor_,
            "n_params": len(self.region_mean_) + len(self.season_factor_),
        }

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "M0Lookup":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        m = cls(season_mode=d["season_mode"], smoothing=d["smoothing"])
        m.global_mean_ = d["global_mean"]
        m.region_mean_ = d["region_mean"]
        m.season_factor_ = d["season_factor"]
        return m


def region_oracle(train: pd.DataFrame, test: pd.DataFrame, target: str = "화재건수") -> np.ndarray:
    """지역 축의 이론적 상한 — 홀드아웃 자체의 지역평균을 쓴다(오라클)."""
    m = test.assign(_rid=region_id(test)).groupby("_rid")[target].mean()
    return region_id(test).map(m).fillna(test[target].mean()).to_numpy(dtype=float)
