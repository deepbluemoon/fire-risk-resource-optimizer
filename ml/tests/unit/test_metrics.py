"""T014 — 지표 함수 단위 테스트. 해석적으로 답을 아는 케이스로 고정한다."""
from __future__ import annotations

import numpy as np
import pytest

from fire_ml.eval import metrics as M


def test_poisson_deviance_perfect_is_zero():
    y = np.array([0, 1, 2, 3, 0])
    assert M.poisson_deviance(y, y) == pytest.approx(0.0, abs=1e-9)


def test_poisson_deviance_penalises_underprediction():
    y = np.array([2.0, 2.0, 2.0])
    assert M.poisson_deviance(y, np.full(3, 1.0)) > M.poisson_deviance(y, np.full(3, 1.8))


def test_poisson_deviance_handles_zero_cells():
    """0건 셀이 67.6% 다 — log(0) 로 nan 이 나오면 안 된다."""
    d = M.poisson_deviance(np.zeros(100), np.full(100, 0.4))
    assert np.isfinite(d) and d == pytest.approx(2 * 0.4, abs=1e-9)


def test_calibration_ratio():
    assert M.calibration_ratio([1, 2, 3], [2, 2, 2]) == pytest.approx(1.0)
    assert M.calibration_ratio([1, 1, 1], [2, 2, 2]) == pytest.approx(2.0)


def test_recall_at_k_perfect_ranking():
    y = np.array([0, 0, 0, 0, 0, 0, 0, 0, 5, 5])   # 상위 20% 가 전부 포착
    s = np.arange(10, dtype=float)
    assert M.recall_at_k(y, s, 0.20) == pytest.approx(1.0)


def test_recall_at_k_random_ranking_is_near_base():
    rng = np.random.default_rng(0)
    y = rng.poisson(0.4, 20000).astype(float)
    assert M.recall_at_k(y, rng.random(20000), 0.20) == pytest.approx(0.20, abs=0.02)


def test_brier_bounds():
    assert M.brier_score([1, 0], [1, 0]) == pytest.approx(0.0)
    assert M.brier_score([1, 0], [0, 1]) == pytest.approx(1.0)


def test_calibration_curve_max_dev_perfect():
    rng = np.random.default_rng(1)
    p = rng.random(20000)
    y = (rng.random(20000) < p).astype(float)
    assert M.calibration_curve_max_dev(y, p, 10) < 5.0    # M5 합격 기준 5%p


def test_calibration_curve_max_dev_detects_bias():
    p = np.full(1000, 0.9)
    y = np.zeros(1000)
    assert M.calibration_curve_max_dev(y, p, 10) == pytest.approx(90.0, abs=1e-6)


def test_log_mae_not_dominated_by_tail():
    """절대 MAE 는 max 4,743억에 끌려다닌다 — 로그 스케일은 그렇지 않다."""
    y = np.array([1.0, 1.0, 1e9])
    near = np.array([1.0, 1.0, 5e8])
    far = np.array([1e9, 1e9, 1e9])
    assert M.log_mae(y, near) < M.log_mae(y, far)


def test_topk_accuracy():
    classes = np.array(["a", "b", "c", "d"])
    proba = np.array([[0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4]])
    assert M.topk_accuracy(["a", "d"], proba, classes, k=1) == pytest.approx(1.0)
    assert M.topk_accuracy(["c", "a"], proba, classes, k=3) == pytest.approx(0.5)


def test_class_lift_perfect_and_useless():
    y = np.array(["x"] * 10 + ["o"] * 90)
    perfect = np.concatenate([np.ones(10), np.zeros(90)])
    assert M.class_lift(y, perfect, "x", k=0.10) == pytest.approx(10.0)
    flat = np.zeros(100)
    assert M.class_lift(y, flat, "x", k=0.10) == pytest.approx(10.0)  # 안정 정렬 → 앞 10개


def test_occurrence_report_keys():
    r = M.occurrence_report([0, 1, 2], [0.5, 0.5, 0.5], "probe")
    assert set(r) == {"name", "poisson_deviance", "calibration", "recall@20%", "recall@10%"}
