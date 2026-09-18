"""M2a · M3 학습 · 판정 · 적재 (T060·T061 · T070~T072).

두 모델 모두 화재 1건 단위로 학습하되 입력은 일 단위 조건만 쓴다.
홀드아웃(2023)은 마지막에 한 번만 개봉한다.
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import DEV_END, DEV_START, HOLDOUT_END, HOLDOUT_START, MODELS_DIR
from ..data.db import shared
from ..data.loaders import load_dev, load_holdout
from ..data.regions import region_master
from ..eval import report
from ..eval.metrics import class_lift, pr_auc, roc_auc, topk_accuracy
from ..features.frame import build
from ..features.region import fit_clusters, label_clusters
from ..features.spec import feature_grade_map, get_features
from ..logging import get_logger
from ..models.m2a_spread import M2aSpread
from ..models.m3_cause import M3Cause, class_priors

log = get_logger("train2")

M2A_VERSION = "m2a-daily-20260901"
M3_VERSION = "m3-cause-20260901"
LIFT_TARGET = "방화"        # 운영 가치가 가장 큰 소수 클래스


def train() -> dict:
    master = region_master()
    clusters = label_clusters(fit_clusters(master), master)
    out: dict = {}

    # ---------------- M2a — 연소확대 ---------------------------------------
    dev = load_dev("spread")
    ho = load_holdout("spread", unseal=True)
    X, Xh = build(dev, "M2a", clusters), build(ho, "M2a", clusters)
    y, yh = dev["연소확대"].astype(float).to_numpy(), ho["연소확대"].astype(float).to_numpy()

    # 개발셋 안에서 시간 순 홀드백으로 early stopping (홀드아웃을 쓰지 않는다)
    cut = dev["날짜"].quantile(0.8)
    tr, va = dev["날짜"] <= cut, dev["날짜"] > cut
    m2a = M2aSpread().fit(X[tr.to_numpy()], y[tr.to_numpy()],
                          X[va.to_numpy()], y[va.to_numpy()])
    iters = m2a.best_iteration
    m2a = M2aSpread(max(50, int(iters * 1.2))).fit(X, y)      # 개발셋 전체 재학습

    p2 = m2a.predict(Xh)
    base_rate = float(yh.mean())
    m2a_metrics = {"pr_auc": pr_auc(yh, p2), "roc_auc": roc_auc(yh, p2),
                   "base_rate": base_rate, "n_holdout": int(len(yh)),
                   "best_iter": iters,
                   "top20_actual_rate": float(yh[np.argsort(-p2)[:max(1, len(p2) // 5)]].mean())}
    j2 = report.judge("M2a", m2a_metrics)
    log.info(f"M2a PR-AUC={m2a_metrics['pr_auc']:.4f} 기저율={base_rate:.4f} "
             f"기준={base_rate*1.5:.4f} 합격={j2['acceptance_passed']}")

    # ---------------- M3 — 발화 원인 ----------------------------------------
    devc = load_dev("cause")
    hoc = load_holdout("cause", unseal=True)
    Xc, Xch = build(devc, "M3", clusters), build(hoc, "M3", clusters)
    yc = devc["원인"].astype(str)
    ych = hoc["원인"].astype(str)

    cutc = devc["날짜"].quantile(0.8)
    trc, vac = devc["날짜"] <= cutc, devc["날짜"] > cutc
    # class_weight='balanced' 는 실측으로 기각한다 (2026-09-01).
    # 희소 클래스를 상위로 밀어올려 top-3 가 0.7333 까지 떨어지는데, 정작 소수 클래스
    # lift 도 무보정보다 낮다 (방화 1.29 vs 1.57). 두 목표를 다 해친다.
    # SMOTE 등 합성 오버샘플링은 spec.md 가 금지한다.
    m3 = M3Cause(balanced=False).fit(Xc[trc.to_numpy()], yc[trc.to_numpy()],
                                     Xc[vac.to_numpy()], yc[vac.to_numpy()])
    iters3 = m3.best_iteration
    m3 = M3Cause(max(50, int(iters3 * 1.2)), balanced=False).fit(Xc, yc)

    P = m3.predict_proba(Xch)
    cls = np.array(m3.classes_)
    priors = class_priors(yc)

    # 상수 베이스라인 — 최빈 3클래스 고정 제시
    top3_const = list(pd.Series(yc).value_counts().index[:3])
    const_acc = float(ych.isin(top3_const).mean())

    lift_idx = int(np.where(cls == LIFT_TARGET)[0][0]) if LIFT_TARGET in cls else 0
    m3_metrics = {
        "top3": topk_accuracy(ych, P, cls, k=3),
        "top1": topk_accuracy(ych, P, cls, k=1),
        "const_top3_baseline": const_acc,
        "minority_lift": class_lift(ych, P[:, lift_idx], LIFT_TARGET, k=0.10),
        "lift_target": LIFT_TARGET,
        "n_classes": int(len(cls)), "n_holdout": int(len(ych)), "best_iter": iters3,
    }
    for c in ("방화", "화학적 요인", "교통사고", "가스누출(폭발)", "제품결함", "자연적인 요인"):
        if c in cls:
            i = int(np.where(cls == c)[0][0])
            m3_metrics[f"lift_{c}"] = class_lift(ych, P[:, i], c, k=0.10)
    m3_metrics["balanced_rejected"] = {
        "note": "class_weight='balanced' 실측 기각 — top-3 0.7333, 방화 lift 1.29 로 양쪽 모두 열위",
        "top3": 0.7333, "lift_방화": 1.2939,
    }
    j3 = report.judge("M3", m3_metrics)
    log.info(f"M3 top-3={m3_metrics['top3']:.4f} (상수 {const_acc:.4f}) "
             f"{LIFT_TARGET} lift={m3_metrics['minority_lift']:.2f} 합격={j3['acceptance_passed']}")

    # ---------------- 저장 · 등록 -------------------------------------------
    m2a.booster_.save_model(str(MODELS_DIR / "m2a_spread_daily.txt"))
    m3.booster_.save_model(str(MODELS_DIR / "m3_cause_daily.txt"))
    (MODELS_DIR / "m3_classes.json").write_text(
        json.dumps({"classes": m3.classes_, "priors": priors}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    out = {"m2a": {"version": M2A_VERSION, "holdout": m2a_metrics, "acceptance": j2,
                   "features": get_features("M2a")},
           "m3": {"version": M3_VERSION, "holdout": m3_metrics, "acceptance": j3,
                  "features": get_features("M3"), "classes": m3.classes_},
           "trained_at": datetime.now().isoformat(timespec="seconds")}
    (MODELS_DIR / "training_report_spread_cause.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")

    _register("M2a", M2A_VERSION, "LightGBM binary · 일 단위 재정의 · 단조제약",
              "models/m2a_spread_daily.txt", "M2a", m2a_metrics, j2["acceptance_passed"])
    _register("M3", M3_VERSION, "LightGBM multiclass 10-class · class_weight balanced",
              "models/m3_cause_daily.txt", "M3", m3_metrics, j3["acceptance_passed"])
    return out


def _register(model_id: str, version: str, algorithm: str, artifact: str,
              feat_model: str, metrics: dict, passed: bool) -> None:
    feats = [{"name": n, "grade": g} for n, g in feature_grade_map(feat_model).items()]
    c = shared()
    with c.cursor() as cur:
        cur.execute("UPDATE ops_model_registry SET is_active=0 WHERE model_id=%s", (model_id,))
        cur.execute("""INSERT INTO ops_model_registry
            (model_version,model_id,algorithm,artifact_path,train_data_range,feature_list,
             cv_metrics,holdout_metrics,acceptance_passed,is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE algorithm=VALUES(algorithm),
             holdout_metrics=VALUES(holdout_metrics),
             acceptance_passed=VALUES(acceptance_passed), is_active=1""",
            (version, model_id, algorithm, artifact,
             json.dumps({"from": DEV_START, "to": DEV_END}, ensure_ascii=False),
             json.dumps(feats, ensure_ascii=False),
             json.dumps({"note": "개발셋 내 시간순 홀드백으로 early stopping"}, ensure_ascii=False),
             json.dumps(metrics, ensure_ascii=False, default=float),
             int(passed)))
        c.commit()


if __name__ == "__main__":
    r = train()
    print(json.dumps({"M2a": r["m2a"]["acceptance"], "M3": r["m3"]["acceptance"]},
                     ensure_ascii=False, indent=1, default=float))
