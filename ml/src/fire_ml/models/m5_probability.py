"""M5 — 화재 발생 확률 P(N≥1) (plan.md §M-3).

화면이 요구하는 값은 "기대 건수 0.43건"이 아니라 "발생 확률 38%"다 (SC-002).

두 경로를 실측 비교한다:
  ① 유도  — M1 의 λ 에서 1 − exp(−λ)
  ② 직접  — LightGBM binary, M1 과 동일 피처

과산포비 1.195, 0셀 67.6% 라 Poisson 가정이 하단에서 어긋날 수 있으므로 논증이 아니라 실측으로 정한다.
확률을 숫자로 노출하므로 **순위보다 캘리브레이션이 우선**이다 — isotonic 보정을 건다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ..eval.cv import expanding_splits
from ..eval.metrics import brier_score, calibration_curve_max_dev, pr_auc, roc_auc
from ..features.frame import build
from ..models.m0_lookup import M0Lookup
from ..models.m1_count import M1GBM, m0_offset

TARGET = "화재건수"


def derived(mu: np.ndarray) -> np.ndarray:
    """경로 ① — Poisson 에서 유도."""
    return 1.0 - np.exp(-np.clip(np.asarray(mu, float), 0, None))


class M5Direct:
    """경로 ② — 이진 직접 적합."""

    name = "M5-direct"
    PARAMS = dict(objective="binary", metric="binary_logloss", learning_rate=0.04,
                  num_leaves=31, max_depth=7, min_data_in_leaf=200,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=5.0, verbose=-1, seed=20260901, num_threads=4)

    def __init__(self, n_estimators: int = 300):
        self.n_estimators = n_estimators
        self.booster_ = None
        self.features_: list[str] = []

    def fit(self, X, y_bin, X_val=None, y_val=None):
        import lightgbm as lgb
        from ..models.m1_count import MONOTONE
        self.features_ = list(X.columns)
        p = dict(self.PARAMS, monotone_constraints=[MONOTONE.get(c, 0) for c in self.features_])
        ds = lgb.Dataset(X, label=np.asarray(y_bin, float), free_raw_data=False)
        valid, cbs = None, []
        if X_val is not None:
            valid = [lgb.Dataset(X_val, label=np.asarray(y_val, float), reference=ds)]
            cbs = [lgb.early_stopping(40, verbose=False)]
        self.booster_ = lgb.train(p, ds, num_boost_round=self.n_estimators,
                                  valid_sets=valid, callbacks=cbs)
        return self

    def predict(self, X) -> np.ndarray:
        return self.booster_.predict(X[self.features_])

    @property
    def best_iteration(self) -> int:
        return int(self.booster_.best_iteration or self.booster_.num_trees())


class Calibrator:
    """isotonic 보정. 개발셋 out-of-fold 예측으로 적합한다 (in-sample 적합은 무의미).

    **극단 구간 Laplace 보정**: isotonic 의 최상단 구간에 속한 표본이 전부 양성이면
    출력이 정확히 1.0 이 된다. 화면에 "발생 확률 100%" 를 띄우는 것은 표본 해상도를
    넘는 확신이다 — 구간당 평균 표본 수 m 에 대해 [1/(m+2), 1−1/(m+2)] 로 자른다
    (Laplace 규칙). 실측: 표본 114,707 / 구간 194 → m≈591 → 상한 0.9983.
    """

    def __init__(self):
        self.iso_: IsotonicRegression | None = None
        self.lo_: float = 1e-6
        self.hi_: float = 1 - 1e-6

    def _set_bounds(self, n_samples: int, n_knots: int) -> None:
        m = max(1.0, n_samples / max(1, n_knots))
        eps = 1.0 / (m + 2.0)
        self.lo_, self.hi_ = float(eps), float(1.0 - eps)

    def fit(self, p_oof: np.ndarray, y_bin: np.ndarray) -> "Calibrator":
        p_oof = np.asarray(p_oof, float)
        self.iso_ = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        self.iso_.fit(p_oof, np.asarray(y_bin, float))
        self._set_bounds(len(p_oof), len(self.iso_.X_thresholds_))
        return self

    def apply(self, p: np.ndarray) -> np.ndarray:
        return np.clip(self.iso_.predict(np.asarray(p, float)), self.lo_, self.hi_)

    def to_dict(self) -> dict:
        return {"x": [float(v) for v in self.iso_.X_thresholds_],
                "y": [float(v) for v in self.iso_.y_thresholds_],
                "clip_lo": self.lo_, "clip_hi": self.hi_}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibrator":
        c = cls()
        c.iso_ = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        c.iso_.fit(np.asarray(d["x"], float), np.asarray(d["y"], float))
        c.lo_ = float(d.get("clip_lo", 1e-6))
        c.hi_ = float(d.get("clip_hi", 1 - 1e-6))
        return c


def oof_predictions(dev: pd.DataFrame, clusters: pd.DataFrame, n_estimators: int) -> dict:
    """5폴드 out-of-fold 예측 — 보정과 경로 비교에 함께 쓴다."""
    n = len(dev)
    p_der = np.full(n, np.nan)
    p_dir = np.full(n, np.nan)
    y = (dev[TARGET].to_numpy(float) >= 1).astype(float)

    for tr, va in expanding_splits(dev["날짜"]):
        d_tr, d_va = dev.iloc[tr], dev.iloc[va]
        m0 = M0Lookup(season_mode="season").fit(d_tr)
        X_tr, X_va = build(d_tr, "M1", clusters), build(d_va, "M1", clusters)
        o_tr, o_va = m0_offset(m0, d_tr), m0_offset(m0, d_va)

        m1 = M1GBM(n_estimators).fit(X_tr, d_tr[TARGET].to_numpy(float), o_tr,
                                     X_va, d_va[TARGET].to_numpy(float), o_va)
        p_der[va] = derived(m1.predict(X_va, o_va))

        m5 = M5Direct(n_estimators).fit(X_tr, (d_tr[TARGET] >= 1).astype(float),
                                        X_va, (d_va[TARGET] >= 1).astype(float))
        p_dir[va] = m5.predict(X_va)

    ok = ~np.isnan(p_der)
    return {"mask": ok, "y": y, "derived": p_der, "direct": p_dir}


def compare(oof: dict) -> dict:
    ok, y = oof["mask"], oof["y"]
    out = {}
    for path in ("derived", "direct"):
        p = oof[path][ok]
        cal = Calibrator().fit(p, y[ok])
        pc = cal.apply(p)
        out[path] = {
            "brier_raw": brier_score(y[ok], p),
            "brier_calibrated": brier_score(y[ok], pc),
            "calib_max_dev_raw_pp": calibration_curve_max_dev(y[ok], p),
            "calib_max_dev_calibrated_pp": calibration_curve_max_dev(y[ok], pc),
            "roc_auc": roc_auc(y[ok], p),
            "pr_auc": pr_auc(y[ok], p),
        }
    return out
