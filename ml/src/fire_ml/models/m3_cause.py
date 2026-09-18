"""M3 — 발화 원인 10-class (plan.md §M-7).

**기존 합격기준의 결함**: `top-3 > 85%` 인데, 최빈 3클래스(부주의 54.25% · 전기적 26.71% ·
기계적 11.10%)를 고정 제시하기만 해도 홀드아웃 91.70% 가 나온다. 피처를 하나도 안 보는
상수 예측기가 기준을 넘는다.

따라서 주 지표를 **소수 클래스 lift** 로 바꾼다 — 운영 가치는 "부주의를 또 맞히는 것"이 아니라
"오늘 이 조건에서 방화 확률이 평소의 3배"를 알리는 데 있다.

SMOTE 등 합성 오버샘플링 금지 (spec.md 명시). class_weight 로 처리한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "원인"
MINORITY = ["방화", "화학적 요인", "가스누출(폭발)", "제품결함", "자연적인 요인", "교통사고"]


class M3Cause:
    name = "M3"
    PARAMS = dict(objective="multiclass", metric="multi_logloss", learning_rate=0.05,
                  num_leaves=31, max_depth=7, min_data_in_leaf=150,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=5.0, verbose=-1, seed=20260901, num_threads=4)

    def __init__(self, n_estimators: int = 400, balanced: bool = True):
        self.n_estimators = n_estimators
        self.balanced = balanced
        self.booster_ = None
        self.classes_: list[str] = []
        self.features_: list[str] = []

    def _weights(self, y: pd.Series) -> np.ndarray:
        if not self.balanced:
            return np.ones(len(y))
        counts = y.value_counts()
        w = (len(y) / (len(counts) * counts)).to_dict()
        return y.map(w).to_numpy(float)

    def fit(self, X, y, X_val=None, y_val=None) -> "M3Cause":
        import lightgbm as lgb
        y = pd.Series(y).astype(str).reset_index(drop=True)
        self.classes_ = sorted(y.unique())
        idx = {c: i for i, c in enumerate(self.classes_)}
        self.features_ = list(X.columns)
        p = dict(self.PARAMS, num_class=len(self.classes_))
        ds = lgb.Dataset(X, label=y.map(idx).to_numpy(int), weight=self._weights(y),
                         free_raw_data=False)
        valid, cbs = None, []
        if X_val is not None:
            yv = pd.Series(y_val).astype(str).reset_index(drop=True)
            valid = [lgb.Dataset(X_val, label=yv.map(idx).fillna(0).to_numpy(int), reference=ds)]
            cbs = [lgb.early_stopping(50, verbose=False)]
        self.booster_ = lgb.train(p, ds, num_boost_round=self.n_estimators,
                                  valid_sets=valid, callbacks=cbs)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster_.predict(X[self.features_])

    def top3(self, X: pd.DataFrame, priors: dict[str, float] | None = None) -> list[list[dict]]:
        """상위 3 후보 + 평시 대비 배율. lift 가 실질 가치 지표다."""
        P = self.predict_proba(X)
        cls = np.array(self.classes_)
        order = np.argsort(-P, axis=1)[:, :3]
        out = []
        for i in range(len(P)):
            row = []
            for r, j in enumerate(order[i], 1):
                c = str(cls[j])
                prob = float(P[i, j])
                base = float(priors.get(c, 0.0)) if priors else 0.0
                row.append({"rank": r, "cause_class": c, "probability": prob,
                            "lift_vs_base": round(prob / base, 4) if base > 0 else None})
            out.append(row)
        return out

    @property
    def best_iteration(self) -> int:
        return int(self.booster_.best_iteration or self.booster_.num_trees())


def class_priors(y) -> dict[str, float]:
    s = pd.Series(y).astype(str)
    return (s.value_counts(normalize=True)).to_dict()
