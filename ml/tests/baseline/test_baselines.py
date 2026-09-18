"""T016 — 착수 게이트 (quickstart.md §4).

이 테스트가 통과하기 전에는 어떤 모델도 학습하지 않는다.
지표 함수나 분할 경계가 문서와 다르다는 뜻이며, 그 상태에서 학습한 모델은 비교 대상이 없다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fire_ml.data.loaders import load_dev, load_holdout
from fire_ml.eval.metrics import poisson_deviance, recall_at_k, topk_accuracy
from fire_ml.models.m0_lookup import M0Lookup, region_oracle

TOL_DEV = 5e-4
TOL_REC = 5e-3


@pytest.fixture(scope="module")
def occ():
    return load_dev("occurrence"), load_holdout("occurrence", unseal=True)


def test_dataset_shape(occ):
    dev, ho = occ
    assert len(dev) == 183_230, "개발셋 2021-01~2022-12 = 251 시군구 × 730일"
    assert len(ho) == 91_615, "홀드아웃 2023 = 251 시군구 × 365일"
    assert (dev["시도"] + "|" + dev["시군구"]).nunique() == 251


def test_global_mean_deviance(occ):
    dev, ho = occ
    mu = np.full(len(ho), dev["화재건수"].mean())
    assert poisson_deviance(ho["화재건수"], mu) == pytest.approx(1.0349, abs=TOL_DEV)


def test_region_mean_deviance(occ):
    dev, ho = occ
    m = M0Lookup(season_mode="none").fit(dev)
    assert len(m.region_mean_) == 251
    assert poisson_deviance(ho["화재건수"], m.predict(ho)) == pytest.approx(0.9164, abs=TOL_DEV)


def test_region_season_deviance(occ):
    """M0 채택안. M1 의 실질 경쟁자다."""
    dev, ho = occ
    m = M0Lookup(season_mode="season").fit(dev)
    assert len(m.region_mean_) + len(m.season_factor_) == 255
    assert poisson_deviance(ho["화재건수"], m.predict(ho)) == pytest.approx(0.9108, abs=TOL_DEV)


def test_region_oracle_deviance(occ):
    """지역 축의 이론적 상한. 날씨 축의 여유는 0.9108 - 0.9078 = 0.003 뿐이다."""
    dev, ho = occ
    assert poisson_deviance(ho["화재건수"], region_oracle(dev, ho)) == pytest.approx(0.9078, abs=TOL_DEV)


def test_region_mean_recall_at_20(occ):
    dev, ho = occ
    m = M0Lookup(season_mode="none").fit(dev)
    assert recall_at_k(ho["화재건수"], m.predict(ho), 0.20) == pytest.approx(0.370, abs=TOL_REC)


def test_season_beats_region_mean(occ):
    """계절은 쪼개지 말고 곱한다 — 곱셈 보정이 지역평균을 이겨야 한다."""
    dev, ho = occ
    d0 = poisson_deviance(ho["화재건수"], M0Lookup(season_mode="none").fit(dev).predict(ho))
    d1 = poisson_deviance(ho["화재건수"], M0Lookup(season_mode="season").fit(dev).predict(ho))
    assert d1 < d0


def test_constant_top3_cause_accuracy():
    """M3 의 합격기준을 정하는 상수 베이스라인.

    피처를 하나도 안 보고 최빈 3클래스를 고정 제시하기만 해도 91.7% 가 나온다.
    기존 기준 'top-3 > 85%' 는 이 상수 예측기에게 진다.
    """
    from fire_ml.config import DATA_DIR
    tr = pd.read_csv(DATA_DIR / "cause_train.csv", usecols=["원인"])
    te = pd.read_csv(DATA_DIR / "cause_test.csv", usecols=["원인"])
    top3 = list(tr["원인"].value_counts().index[:3])
    assert top3 == ["부주의", "전기적 요인", "기계적 요인"]
    acc = te["원인"].isin(top3).mean()
    assert acc == pytest.approx(0.9170, abs=2e-3)


def test_topk_accuracy_matches_constant_baseline():
    """topk_accuracy() 지표 함수가 상수 베이스라인과 같은 값을 낸다."""
    from fire_ml.config import DATA_DIR
    tr = pd.read_csv(DATA_DIR / "cause_train.csv", usecols=["원인"])
    te = pd.read_csv(DATA_DIR / "cause_test.csv", usecols=["원인"])
    classes = np.array(tr["원인"].value_counts().index)
    prior = (tr["원인"].value_counts(normalize=True).reindex(classes)).to_numpy()
    proba = np.tile(prior, (len(te), 1))
    assert topk_accuracy(te["원인"], proba, classes, k=3) == pytest.approx(0.9170, abs=2e-3)
