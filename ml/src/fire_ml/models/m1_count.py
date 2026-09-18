"""M1 — 기대 발생 건수 (plan.md §M-2).

두 후보를 같은 5폴드 CV 하네스에서 경쟁시킨다.

**핵심 설계**: 두 후보 모두 `log(M0 λ)` 를 offset 으로 쓴다.
M0(지역평균 × 전역 계절계수)은 홀드아웃 0.9108 로 이미 검증된 골격이고,
날씨 항의 계수가 전부 0 이면 M1 은 정확히 M0 가 된다 — **하방이 막혀 있다.**

이것이 `log(인구)` 단독 offset 보다 나은 이유:
지역 축 상한(oracle 0.9078)과 M0(0.9108) 사이 여유가 deviance 0.003 뿐이라,
지역·계절 구조를 모델이 처음부터 다시 찾게 하면 그 여유를 찾기 전에 과적합한다.
B2 실측(지역×월 셀 = 트리의 자연 행동)이 test 0.9303 으로 최악이었던 것이 그 증거다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..eval.cv import STRUCTURAL_GAP_M0, expanding_splits, is_overfit
from ..eval.metrics import occurrence_report, poisson_deviance
from ..features.frame import build
from ..features.spec import get_features
from .m0_lookup import M0Lookup

TARGET = "화재건수"

# 물리적으로 방향이 확정된 변수 — 단조 제약을 건다 (plan.md §M-2 후보 B)
MONOTONE = {
    "실효습도": -1, "최소습도": -1, "무강수일수": +1, "일교차": +1,
    "풍속건조지수": +1, "건조지속강도": +1, "한랭건조": +1, "임야건조": +1,
    "강수량": -1,
}

# GLM 이 쓰는 날씨 항 (M0 offset 위에 얹는 곱셈 보정)
GLM_TERMS = ["건조도", "건조도2", "최소습도결핍", "무강수sqrt", "일교차", "풍속",
             "풍속건조지수", "건조지속강도", "한랭건조", "임야건조", "강수있음"]


def _glm_design(X: pd.DataFrame) -> pd.DataFrame:
    eh = X["실효습도"].astype(float)
    d = pd.DataFrame(index=X.index)
    d["건조도"] = ((100 - eh) / 100).clip(0, 1)
    d["건조도2"] = d["건조도"] ** 2
    d["최소습도결핍"] = ((100 - X["최소습도"].astype(float)) / 100).clip(0, 1)
    d["무강수sqrt"] = np.sqrt(X["무강수일수"].astype(float).clip(lower=0))
    d["일교차"] = X["일교차"].astype(float) / 10.0
    d["풍속"] = X["풍속"].astype(float)
    for c in ("풍속건조지수", "건조지속강도", "한랭건조", "임야건조"):
        d[c] = X[c].astype(float)
    d["강수있음"] = (X["강수량"].astype(float) > 0).astype(float)
    med = d.median(numeric_only=True)
    return d.fillna(med).fillna(0.0)


class M1GLM:
    """후보 A — 곱셈 구조 Poisson GLM. 계수가 곧 설명이라 US1 시나리오 2 가 공짜로 나온다."""

    name = "M1-GLM"

    def __init__(self):
        self.res_ = None
        self.terms_ = GLM_TERMS

    def fit(self, X: pd.DataFrame, y, offset_log: np.ndarray) -> "M1GLM":
        import statsmodels.api as sm
        D = sm.add_constant(_glm_design(X), has_constant="add")
        self.terms_ = list(D.columns)
        self.res_ = sm.GLM(np.asarray(y, float), D.to_numpy(float),
                           family=sm.families.Poisson(),
                           offset=offset_log).fit(maxiter=100)
        return self

    def predict(self, X: pd.DataFrame, offset_log: np.ndarray) -> np.ndarray:
        import statsmodels.api as sm
        D = sm.add_constant(_glm_design(X), has_constant="add")
        return self.res_.predict(D.to_numpy(float), offset=offset_log)

    def coefficients(self) -> dict:
        return dict(zip(self.terms_, [float(v) for v in self.res_.params]))


class M1GBM:
    """후보 B — LightGBM Poisson. §4-2 규제 + 단조 제약."""

    name = "M1-GBM"
    PARAMS = dict(objective="poisson", metric="poisson", learning_rate=0.04,
                  num_leaves=31, max_depth=7, min_data_in_leaf=200,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=5.0, verbose=-1, seed=20260901, num_threads=4)

    def __init__(self, n_estimators: int = 400):
        self.n_estimators = n_estimators
        self.booster_ = None
        self.features_: list[str] = []

    def _mono(self, cols) -> list[int]:
        return [MONOTONE.get(c, 0) for c in cols]

    def fit(self, X, y, offset_log, X_val=None, y_val=None, off_val=None):
        import lightgbm as lgb
        self.features_ = list(X.columns)
        p = dict(self.PARAMS, monotone_constraints=self._mono(self.features_))
        ds = lgb.Dataset(X, label=np.asarray(y, float), init_score=offset_log,
                         free_raw_data=False)
        valid, cbs = None, []
        if X_val is not None:
            valid = [lgb.Dataset(X_val, label=np.asarray(y_val, float),
                                 init_score=off_val, reference=ds)]
            cbs = [lgb.early_stopping(40, verbose=False)]
        self.booster_ = lgb.train(p, ds, num_boost_round=self.n_estimators,
                                  valid_sets=valid, callbacks=cbs)
        return self

    def predict(self, X: pd.DataFrame, offset_log: np.ndarray) -> np.ndarray:
        raw = self.booster_.predict(X[self.features_], raw_score=True)
        return np.exp(raw + offset_log)          # init_score 는 예측에 포함되지 않는다

    @property
    def best_iteration(self) -> int:
        return int(self.booster_.best_iteration or self.booster_.num_trees())


def m0_offset(m0: M0Lookup, df: pd.DataFrame) -> np.ndarray:
    """log(M0 λ). 날씨 계수가 0 이면 M1 은 정확히 M0 가 된다."""
    return np.log(np.clip(m0.predict(df), 1e-6, None))


def cross_validate(dev: pd.DataFrame, clusters: pd.DataFrame) -> dict:
    """Expanding-window 5폴드. 하이퍼파라미터·early stopping 을 여기서 결정한다."""
    out: dict = {}
    for cand in ("glm", "gbm"):
        tr_devs, va_devs, iters = [], [], []
        for tr, va in expanding_splits(dev["날짜"]):
            d_tr, d_va = dev.iloc[tr], dev.iloc[va]
            m0 = M0Lookup(season_mode="season").fit(d_tr)      # 폴드마다 다시 적합 (누수 차단)
            X_tr, X_va = build(d_tr, "M1", clusters), build(d_va, "M1", clusters)
            o_tr, o_va = m0_offset(m0, d_tr), m0_offset(m0, d_va)
            y_tr, y_va = d_tr[TARGET].to_numpy(float), d_va[TARGET].to_numpy(float)

            if cand == "glm":
                m = M1GLM().fit(X_tr, y_tr, o_tr)
                p_tr, p_va = m.predict(X_tr, o_tr), m.predict(X_va, o_va)
            else:
                m = M1GBM().fit(X_tr, y_tr, o_tr, X_va, y_va, o_va)
                p_tr, p_va = m.predict(X_tr, o_tr), m.predict(X_va, o_va)
                iters.append(m.best_iteration)
            tr_devs.append(poisson_deviance(y_tr, p_tr))
            va_devs.append(poisson_deviance(y_va, p_va))

        gap = float(np.mean(va_devs) - np.mean(tr_devs))
        out[cand] = {
            "cv_train_deviance": float(np.mean(tr_devs)),
            "cv_valid_deviance": float(np.mean(va_devs)),
            "gap": gap,
            "structural_gap_m0": STRUCTURAL_GAP_M0,
            "excess_gap": round(gap - STRUCTURAL_GAP_M0, 4),
            "overfit": is_overfit(gap),
            "fold_valid": [float(v) for v in va_devs],
            "best_iters": iters or None,
            "mean_best_iter": int(np.mean(iters)) if iters else None,
        }
    return out


def fit_final(dev: pd.DataFrame, clusters: pd.DataFrame, candidate: str,
              n_estimators: int | None = None):
    """개발셋 24개월 전체로 최종 재학습."""
    m0 = M0Lookup(season_mode="season").fit(dev)
    X = build(dev, "M1", clusters)
    off = m0_offset(m0, dev)
    y = dev[TARGET].to_numpy(float)
    model = M1GLM().fit(X, y, off) if candidate == "glm" \
        else M1GBM(n_estimators or 400).fit(X, y, off)
    return model, m0


def evaluate(model, m0: M0Lookup, holdout: pd.DataFrame, clusters: pd.DataFrame) -> dict:
    X = build(holdout, "M1", clusters)
    mu = model.predict(X, m0_offset(m0, holdout))
    return occurrence_report(holdout[TARGET], mu, model.name)
