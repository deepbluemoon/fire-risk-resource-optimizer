"""전 모델 학습 · 판정 · 적재 (T036~T043).

홀드아웃은 마지막에 한 번만 개봉한다.
"""
from __future__ import annotations

import json
import pickle
import subprocess
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import DEV_END, DEV_START, HOLDOUT_END, HOLDOUT_START, MODELS_DIR
from ..data.db import shared
from ..data.loaders import load_dev, load_holdout
from ..data.regions import region_master
from ..eval import report
from ..eval.metrics import brier_score, calibration_curve_max_dev, occurrence_report, roc_auc
from ..features.frame import build
from ..features.region import fit_clusters, label_clusters
from ..features.spec import feature_grade_map, get_features
from ..logging import get_logger
from ..models import m1_count as M1
from ..models import m5_probability as M5
from ..models.m0_lookup import M0Lookup

log = get_logger("train")

M1_VERSION = "m1-gbm-20260901"
M5_VERSION = "m5-derived-20260901"
GRADE_MODEL_VERSION = M1_VERSION   # 등급 경계는 M1 확률 기준으로 다시 잡는다


def _commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def train() -> dict:
    dev = load_dev("occurrence")
    ho = load_holdout("occurrence", unseal=True)
    master = region_master()
    clusters = label_clusters(fit_clusters(master), master)

    # --- 1) M1 두 후보 CV ------------------------------------------------
    cv = M1.cross_validate(dev, clusters)
    for k, v in cv.items():
        log.info(f"CV {k}: valid={v['cv_valid_deviance']:.4f} gap={v['gap']:+.4f} "
                 f"초과={v['excess_gap']:+.4f} 과적합={v['overfit']}")

    winner = min(cv, key=lambda k: cv[k]["cv_valid_deviance"])
    iters = cv[winner].get("mean_best_iter")
    log.info(f"CV 승자: {winner}")

    # --- 2) 최종 재학습 (개발셋 24개월 전체) -------------------------------
    m1, m0 = M1.fit_final(dev, clusters, winner, n_estimators=int(iters * 1.2) if iters else None)
    m1_glm, _ = M1.fit_final(dev, clusters, "glm")     # 설명용 보조 (US1 시나리오 2)

    # --- 3) M5 보정기 (OOF 유도 확률) --------------------------------------
    oof = M5.oof_predictions(dev, clusters, iters or 250)
    cmp_ = M5.compare(oof)
    path = "derived" if cmp_["derived"]["brier_calibrated"] <= cmp_["direct"]["brier_calibrated"] else "direct"
    log.info(f"M5 경로: {path} (derived {cmp_['derived']['brier_calibrated']:.5f} / "
             f"direct {cmp_['direct']['brier_calibrated']:.5f})")
    cal = M5.Calibrator().fit(oof[path][oof["mask"]], oof["y"][oof["mask"]])

    # --- 4) 홀드아웃 개봉 --------------------------------------------------
    X_ho = build(ho, "M1", clusters)
    off_ho = M1.m0_offset(m0, ho)
    mu_ho = m1.predict(X_ho, off_ho)
    y_bin = (ho["화재건수"].to_numpy(float) >= 1).astype(float)

    m1_metrics = occurrence_report(ho["화재건수"], mu_ho, "M1-GBM")
    m0_metrics = occurrence_report(ho["화재건수"], m0.predict(ho), "M0-지역×계절")
    glm_metrics = occurrence_report(ho["화재건수"], m1_glm.predict(X_ho, off_ho), "M1-GLM")

    p_raw = M5.derived(mu_ho)
    p_cal = cal.apply(p_raw)
    p_m0 = M5.derived(m0.predict(ho))
    m5_metrics = {
        "brier": brier_score(y_bin, p_cal),
        "brier_baseline": brier_score(y_bin, p_m0),
        "brier_uncalibrated": brier_score(y_bin, p_raw),
        "calib_max_dev_pp": calibration_curve_max_dev(y_bin, p_cal),
        "calib_max_dev_pp_uncalibrated": calibration_curve_max_dev(y_bin, p_raw),
        "roc_auc": roc_auc(y_bin, p_cal),
        "positive_rate": float(y_bin.mean()),
    }

    j1 = report.judge("M1", m1_metrics)
    j5 = report.judge("M5", m5_metrics)
    log.info(f"M1 합격={j1['acceptance_passed']} · M5 합격={j5['acceptance_passed']}")

    # --- 5) 저장 -----------------------------------------------------------
    MODELS_DIR.mkdir(exist_ok=True)
    m1.booster_.save_model(str(MODELS_DIR / "m1_gbm_poisson.txt"))
    (MODELS_DIR / "m1_glm_coefficients.json").write_text(
        json.dumps(m1_glm.coefficients(), ensure_ascii=False, indent=1), encoding="utf-8")
    (MODELS_DIR / "m5_calibration.json").write_text(
        json.dumps({"path": path, "isotonic": cal.to_dict()}, ensure_ascii=False), encoding="utf-8")
    clusters.to_csv(MODELS_DIR / "region_clusters.csv", index=False)
    m0.save(MODELS_DIR / "m0_lookup_season.json")

    meta = {
        "m1": {"version": M1_VERSION, "candidate": winner, "features": get_features("M1"),
               "feature_grade": feature_grade_map("M1"),
               "offset": "log(M0 λ) — 지역평균 × 전역 계절계수",
               "n_estimators": m1.best_iteration if hasattr(m1, "best_iteration") else None,
               "cv": cv, "holdout": m1_metrics, "acceptance": j1},
        "m1_glm": {"holdout": glm_metrics, "coefficients": m1_glm.coefficients()},
        "m0": {"holdout": m0_metrics},
        "m5": {"version": M5_VERSION, "path": path, "oof_comparison": cmp_,
               "holdout": m5_metrics, "acceptance": j5},
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "data_range": {"dev": [DEV_START, DEV_END], "holdout": [HOLDOUT_START, HOLDOUT_END]},
        "code_commit": _commit(),
    }
    (MODELS_DIR / "training_report.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")

    # --- 6) 등급 경계 재산출 (M1 보정 확률의 홀드아웃 분위수) ------------------
    q = np.quantile(p_cal, [0.40, 0.70, 0.90])
    _write_grades(GRADE_MODEL_VERSION, q)
    log.info(f"등급 경계 갱신 p40={q[0]:.4f} p70={q[1]:.4f} p90={q[2]:.4f}")

    # --- 7) 레지스트리 -----------------------------------------------------
    _register("M1", M1_VERSION, f"LightGBM Poisson + log(M0 λ) offset + 단조제약 ({winner})",
              "models/m1_gbm_poisson.txt", meta["m1"], j1["acceptance_passed"])
    _register("M5", M5_VERSION, f"M1 λ → 1−exp(−λ) → isotonic 보정 ({path})",
              "models/m5_calibration.json", meta["m5"], j5["acceptance_passed"])
    return meta


def _write_grades(version: str, q: np.ndarray) -> None:
    from .seed_reference import GRADE_META
    bounds = [(0.0, q[0]), (q[0], q[1]), (q[1], q[2]), (q[2], 1.0)]
    c = shared()
    with c.cursor() as cur:
        cur.executemany("""INSERT INTO ref_risk_grade
            (model_version,grade,prob_lower,prob_upper,sort_order,color_token,description_ko)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
            prob_lower=VALUES(prob_lower), prob_upper=VALUES(prob_upper),
            color_token=VALUES(color_token), description_ko=VALUES(description_ko)""",
            [(version, g, float(lo), float(hi), i, tok, desc)
             for i, ((g, tok, desc), (lo, hi)) in enumerate(zip(GRADE_META, bounds))])
        c.commit()


def _register(model_id: str, version: str, algorithm: str, artifact: str,
              meta: dict, passed: bool) -> None:
    feats = [{"name": n, "grade": g} for n, g in feature_grade_map("M1").items()]
    c = shared()
    with c.cursor() as cur:
        cur.execute("UPDATE ops_model_registry SET is_active=0 WHERE model_id=%s", (model_id,))
        cur.execute("""INSERT INTO ops_model_registry
            (model_version,model_id,algorithm,artifact_path,train_data_range,feature_list,
             cv_metrics,holdout_metrics,acceptance_passed,code_commit,is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE algorithm=VALUES(algorithm),
             holdout_metrics=VALUES(holdout_metrics), cv_metrics=VALUES(cv_metrics),
             acceptance_passed=VALUES(acceptance_passed), is_active=1""",
            (version, model_id, algorithm, artifact,
             json.dumps({"from": DEV_START, "to": DEV_END}, ensure_ascii=False),
             json.dumps(feats, ensure_ascii=False),
             json.dumps(meta.get("cv", {}), ensure_ascii=False, default=float),
             json.dumps(meta.get("holdout", {}), ensure_ascii=False, default=float),
             int(passed), _commit()))
        c.commit()


if __name__ == "__main__":
    m = train()
    print(json.dumps({"M1": m["m1"]["acceptance"], "M5": m["m5"]["acceptance"]},
                     ensure_ascii=False, indent=1, default=float))
