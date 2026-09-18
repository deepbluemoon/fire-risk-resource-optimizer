"""평가 지표 — 전 모델이 이 모듈만 사용한다 (plan.md §M-9).

셀 단위 정확도로 평가하면 어떤 모델도 실패한다 (0건 셀 67.6%).
캘리브레이션과 랭킹으로 평가한다.
"""
from __future__ import annotations

import numpy as np


def poisson_deviance(y_true, y_pred) -> float:
    """평균 Poisson deviance. 낮을수록 좋다."""
    y = np.asarray(y_true, dtype=float)
    mu = np.clip(np.asarray(y_pred, dtype=float), 1e-10, None)
    # y=0 인 셀에서 log(0) 이 평가되지 않도록 분자를 클리핑한다 (결과는 동일, 경고만 제거)
    term = np.where(y > 0, y * np.log(np.clip(y, 1e-10, None) / mu), 0.0)
    return float(2.0 * np.mean(term - (y - mu)))


def calibration_ratio(y_true, y_pred) -> float:
    """예측합 / 실제합. 1.0 이 완벽. 배분의 전제조건 (SC-011)."""
    return float(np.sum(y_pred) / np.sum(y_true))


def recall_at_k(y_true, y_score, k: float = 0.20) -> float:
    """상위 k 비율 셀이 포착한 실제 발생 건수 비율."""
    y = np.asarray(y_true, dtype=float)
    s = np.asarray(y_score, dtype=float)
    n_top = max(1, int(round(len(s) * k)))
    idx = np.argsort(-s, kind="stable")[:n_top]
    total = y.sum()
    return float(y[idx].sum() / total) if total > 0 else 0.0


def brier_score(y_true, y_prob) -> float:
    """이진 확률 예측의 평균제곱오차. 낮을수록 좋다."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    return float(np.mean((p - y) ** 2))


def calibration_curve_max_dev(y_true, y_prob, n_bins: int = 10) -> float:
    """10분위 캘리브레이션 최대 편차 (%p). M5 합격기준 < 5%p."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_prob, dtype=float)
    order = np.argsort(p, kind="stable")
    max_dev = 0.0
    for chunk in np.array_split(order, n_bins):
        if len(chunk) == 0:
            continue
        max_dev = max(max_dev, abs(p[chunk].mean() - y[chunk].mean()))
    return float(max_dev * 100.0)


def pr_auc(y_true, y_score) -> float:
    from sklearn.metrics import average_precision_score
    return float(average_precision_score(y_true, y_score))


def roc_auc(y_true, y_score) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y_true, y_score))


def log_mae(y_true, y_pred) -> float:
    """로그 스케일 MAE. 절대 MAE 는 극단 꼬리에 지배된다."""
    y = np.log1p(np.clip(np.asarray(y_true, dtype=float), 0, None))
    p = np.log1p(np.clip(np.asarray(y_pred, dtype=float), 0, None))
    return float(np.mean(np.abs(y - p)))


def topk_accuracy(y_true, proba, classes, k: int = 3) -> float:
    """상위 k 후보 안에 정답이 포함된 비율."""
    proba = np.asarray(proba)
    classes = np.asarray(classes)
    topk = classes[np.argsort(-proba, axis=1, kind="stable")[:, :k]]
    return float(np.mean([t in row for t, row in zip(np.asarray(y_true), topk)]))


def class_lift(y_true, y_score, target_class, k: float = 0.10) -> float:
    """상위 k 예측 구간의 해당 클래스 실제 발생률 / 전체 발생률.

    M3 의 실질 가치 지표 — 최빈 클래스를 또 맞히는 것이 아니라
    '오늘 이 조건에서 방화 확률이 평소의 3배'를 알리는 데 가치가 있다.
    """
    y = (np.asarray(y_true) == target_class).astype(float)
    s = np.asarray(y_score, dtype=float)
    base = y.mean()
    if base == 0:
        return 0.0
    n_top = max(1, int(round(len(s) * k)))
    idx = np.argsort(-s, kind="stable")[:n_top]
    return float(y[idx].mean() / base)


def occurrence_report(y_true, mu, name: str = "") -> dict:
    """발생 모델 표준 리포트."""
    return {
        "name": name,
        "poisson_deviance": poisson_deviance(y_true, mu),
        "calibration": calibration_ratio(y_true, mu),
        "recall@20%": recall_at_k(y_true, mu, 0.20),
        "recall@10%": recall_at_k(y_true, mu, 0.10),
    }
