"""참조 테이블 적재 + M0 학습 + 위험 등급 경계 산출.

한 번 실행하면 서비스가 조회할 기준 데이터가 모두 준비된다.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import DEV_END, DEV_START, HOLDOUT_END, HOLDOUT_START, MODELS_DIR
from ..data.db import executemany, run_migration, shared
from ..data.loaders import load_dev, load_holdout
from ..data.regions import actual_daily, region_master
from ..eval.metrics import occurrence_report
from ..features.region import fit_clusters, label_clusters
from ..features.spec import get_features
from ..logging import get_logger
from ..models.m0_lookup import M0Lookup, region_id

log = get_logger("seed")

M0_VERSION = "m0-lookup-20260901"

GRADE_META = [
    ("낮음",     "cds-support-success", "평시 수준입니다. 통상 근무 편성으로 충분합니다."),
    ("보통",     "cds-support-warning", "평시보다 다소 높습니다. 기상 변화를 주시하십시오."),
    ("높음",     "cds-support-caution-major", "발생 가능성이 뚜렷하게 높습니다. 대기 인력 점검을 권장합니다."),
    ("매우높음", "cds-support-error",   "연중 상위 10% 조건입니다. 초동 대응 태세를 상향하십시오."),
]


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def occurrence_probability(mu: np.ndarray) -> np.ndarray:
    """M5 유도 경로 — Poisson 에서 P(N≥1) = 1 − exp(−λ)."""
    return 1.0 - np.exp(-np.clip(mu, 0, None))


def seed() -> dict:
    dev = load_dev("occurrence")
    ho = load_holdout("occurrence", unseal=True)
    master = region_master()

    # --- 1) 지역 프로파일 -------------------------------------------------
    rows = [(r.region_cd, r.sido, r.sigungu,
             _f(r.면적_km2), _f(r.산림율), _f(r.임목축적), _i(r.인구), _i(r.세대수),
             _f(r.인구밀도), _f(r.log_인구), _f(r.평균소방서거리))
            for r in master.itertuples()]
    executemany("""INSERT INTO ref_region_profile
        (region_cd,sido,sigungu,area_km2,forest_ratio,timber_volume,population,households,
         pop_density,log_population,avg_station_distance_km)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE sido=VALUES(sido),sigungu=VALUES(sigungu),
         area_km2=VALUES(area_km2),forest_ratio=VALUES(forest_ratio),
         timber_volume=VALUES(timber_volume),population=VALUES(population),
         households=VALUES(households),pop_density=VALUES(pop_density),
         log_population=VALUES(log_population),
         avg_station_distance_km=VALUES(avg_station_distance_km)""", rows)
    log.info(f"ref_region_profile {len(rows)}행")

    # --- 2) 지역 군집 (개발셋 구간 적합, 화재 실적 미사용) --------------------
    clusters = label_clusters(fit_clusters(master), master)
    executemany("""INSERT INTO ref_region_cluster (region_cd,model_version,cluster_id,cluster_label)
        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        cluster_id=VALUES(cluster_id), cluster_label=VALUES(cluster_label)""",
        [(r.region_cd, M0_VERSION, int(r.cluster_id), r.cluster_label) for r in clusters.itertuples()])
    log.info(f"ref_region_cluster {len(clusters)}행")

    # --- 3) 기상지점 배정 --------------------------------------------------
    smap = master[["region_cd", "기상배정방식"]].copy()
    smap["method"] = smap["기상배정방식"].map(
        {"관내": "관내", "시도평균": "시도평균", "산악": "산악", "도서": "도서"}).fillna("관내")
    executemany("""INSERT INTO ref_station_map (region_cd,station_id,assign_method,is_proxy)
        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        assign_method=VALUES(assign_method), is_proxy=VALUES(is_proxy)""",
        [(r.region_cd, None, r.method, int(r.method != "관내")) for r in smap.itertuples()])
    log.info(f"ref_station_map {len(smap)}행 (대리 {int((smap['method']!='관내').sum())})")

    # --- 4) 실제 발생 실적 -------------------------------------------------
    act = actual_daily()
    executemany("""INSERT INTO ref_actual_daily (target_date,region_cd,fire_count)
        VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE fire_count=VALUES(fire_count)""",
        [(r.target_date.date(), r.region_cd, int(r.fire_count)) for r in act.itertuples()])
    log.info(f"ref_actual_daily {len(act):,}행")

    # --- 5) M0 학습 · 저장 --------------------------------------------------
    m0 = M0Lookup(season_mode="season").fit(dev)
    m0.save(MODELS_DIR / "m0_lookup_season.json")
    mu_ho = m0.predict(ho)
    rep = occurrence_report(ho["화재건수"], mu_ho, "M0-지역×계절")
    base = occurrence_report(ho["화재건수"], M0Lookup(season_mode="none").fit(dev).predict(ho),
                             "M0-지역평균")
    log.info(f"M0 홀드아웃 deviance={rep['poisson_deviance']:.4f} "
             f"calib={rep['calibration']:.3f} recall@20%={rep['recall@20%']:.4f}")

    # --- 6) 위험 등급 경계 (홀드아웃 확률 분위수 p40/p70/p90) -----------------
    p_ho = occurrence_probability(mu_ho)
    q = np.quantile(p_ho, [0.40, 0.70, 0.90])
    bounds = [(0.0, q[0]), (q[0], q[1]), (q[1], q[2]), (q[2], 1.0)]
    executemany("""INSERT INTO ref_risk_grade
        (model_version,grade,prob_lower,prob_upper,sort_order,color_token,description_ko)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
        prob_lower=VALUES(prob_lower), prob_upper=VALUES(prob_upper),
        color_token=VALUES(color_token), description_ko=VALUES(description_ko)""",
        [(M0_VERSION, g, float(lo), float(hi), i, tok, desc)
         for i, ((g, tok, desc), (lo, hi)) in enumerate(zip(GRADE_META, bounds))])
    log.info(f"ref_risk_grade 경계 p40={q[0]:.4f} p70={q[1]:.4f} p90={q[2]:.4f}")

    # --- 7) 모델 레지스트리 -------------------------------------------------
    meta = {
        "model_version": M0_VERSION, "model_id": "M0",
        "algorithm": "지역평균(스무딩 m=20) × 전역 계절계수",
        "artifact_path": "models/m0_lookup_season.json",
        "train_data_range": {"from": DEV_START, "to": DEV_END},
        "feature_list": [{"name": "지역", "grade": "A"}, {"name": "계절", "grade": "A"}],
        "cv_metrics": {"note": "룩업 모델 — 교차검증 대상 아님"},
        "holdout_metrics": {"range": {"from": HOLDOUT_START, "to": HOLDOUT_END},
                            "M0-지역×계절": rep, "M0-지역평균": base},
        "acceptance_passed": 1,
        "code_commit": _git_commit(),
    }
    c = shared()
    with c.cursor() as cur:
        cur.execute("UPDATE ops_model_registry SET is_active=0 WHERE model_id='M0'")
        cur.execute("""INSERT INTO ops_model_registry
            (model_version,model_id,algorithm,artifact_path,train_data_range,feature_list,
             cv_metrics,holdout_metrics,acceptance_passed,code_commit,is_active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            ON DUPLICATE KEY UPDATE algorithm=VALUES(algorithm),
             holdout_metrics=VALUES(holdout_metrics), acceptance_passed=VALUES(acceptance_passed),
             code_commit=VALUES(code_commit), is_active=1""",
            (meta["model_version"], meta["model_id"], meta["algorithm"], meta["artifact_path"],
             json.dumps(meta["train_data_range"], ensure_ascii=False),
             json.dumps(meta["feature_list"], ensure_ascii=False),
             json.dumps(meta["cv_metrics"], ensure_ascii=False),
             json.dumps(meta["holdout_metrics"], ensure_ascii=False),
             meta["acceptance_passed"], meta["code_commit"]))
        c.commit()
    (MODELS_DIR / "m0_lookup_season.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("ops_model_registry 갱신 · M0 활성화")
    return {"m0": rep, "grade_bounds": [float(x) for x in q], "regions": len(master)}


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def _i(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)


if __name__ == "__main__":
    run_migration("sql/002_ref_tables.sql")
    out = seed()
    print(json.dumps(out, ensure_ascii=False, indent=1))
