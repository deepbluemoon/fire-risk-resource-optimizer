"""M2a — 연소확대 확률, 일 단위 재정의 (plan.md §M-4).

원래 정의는 화재 1건 단위였고 피처에 `시각` `장소중분류` `온도` `풍향` 이 들어 있었다.
이는 신고 접수 후에야 알 수 있는 값이라 **새벽 배치에 넣을 수 없다** —
"실시간 추론 API 를 만들지 않는다"는 §7 결정과 정면으로 충돌한다.

재정의: *"오늘 이 시군구에서 화재가 발생한다면 연소확대 확률"*
피처를 일 단위로 확정 가능한 것만 남긴다. 부수 효과로 `발화요인` 이 구조적으로 사라져
M3→M2 캐스케이드 문제(정답 원인으로 검증하면 성능이 낙관적)가 소멸한다.

학습은 화재 1건 단위 행으로 하되(그것이 연소확대의 관측 단위다), 입력은 그 화재가 난
날·지역의 일 단위 조건만 쓴다. 추론은 시군구×일 패널에 그대로 적용한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "연소확대"

# 물리적으로 방향이 확정된 변수
MONOTONE = {"실효습도": -1, "일최소습도": -1, "무강수일수": +1, "강수량": -1,
            "일풍속": +1, "풍속건조지수": +1, "건조지속강도": +1, "임야건조": +1,
            "소방서거리": +1}   # 소방서가 멀수록 확대된다 (실측: 10km↑ 43.3% vs 2km↓ 18.1%)


class M2aSpread:
    name = "M2a"
    PARAMS = dict(objective="binary", metric="average_precision", learning_rate=0.04,
                  num_leaves=31, max_depth=7, min_data_in_leaf=200,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=5.0, verbose=-1, seed=20260901, num_threads=4)

    def __init__(self, n_estimators: int = 500):
        self.n_estimators = n_estimators
        self.booster_ = None
        self.features_: list[str] = []

    def fit(self, X, y, X_val=None, y_val=None) -> "M2aSpread":
        import lightgbm as lgb
        self.features_ = list(X.columns)
        p = dict(self.PARAMS, monotone_constraints=[MONOTONE.get(c, 0) for c in self.features_])
        ds = lgb.Dataset(X, label=np.asarray(y, float), free_raw_data=False)
        valid, cbs = None, []
        if X_val is not None:
            valid = [lgb.Dataset(X_val, label=np.asarray(y_val, float), reference=ds)]
            cbs = [lgb.early_stopping(50, verbose=False)]
        self.booster_ = lgb.train(p, ds, num_boost_round=self.n_estimators,
                                  valid_sets=valid, callbacks=cbs)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster_.predict(X[self.features_])

    @property
    def best_iteration(self) -> int:
        return int(self.booster_.best_iteration or self.booster_.num_trees())
