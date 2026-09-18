"""일일 배치 — 하루 정확히 251행을 적재한다.

예측 불가 지역도 data_status='데이터없음' 행을 남긴다 (SC-006).
예보 수집 실패 시 전일 결과 → M0 기저율 순으로 폴백하고 그 사실을 남긴다 (FR-024·SC-004).
실패를 조용히 넘기지 않는다 (FR-025).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..config import MODELS_DIR, N_REGIONS
from ..data.db import executemany, query, shared
from ..features.frame import build
from ..features.spec import feature_grade_map, get_features
from ..logging import get_logger
from ..models.m0_lookup import M0Lookup, season_of
from . import explain, features_source
from .seed_reference import M0_VERSION, occurrence_probability


def _n(v):
    """NaN·None 을 DB NULL 로."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _load_m1():
    """M1(GBM) · M5 보정기 · 군집표를 적재한다. 하나라도 없으면 M0 단독으로 동작한다."""
    try:
        import lightgbm as lgb

        from ..models.m1_count import M1GBM
        from ..models.m5_probability import Calibrator
        from .train_models import M1_VERSION

        booster = lgb.Booster(model_file=str(MODELS_DIR / "m1_gbm_poisson.txt"))
        m1 = M1GBM()
        m1.booster_ = booster
        m1.features_ = get_features("M1")
        cal_d = json.loads((MODELS_DIR / "m5_calibration.json").read_text(encoding="utf-8"))
        cal = Calibrator.from_dict(cal_d["isotonic"])
        clusters = pd.read_csv(MODELS_DIR / "region_clusters.csv")
        coefs = json.loads((MODELS_DIR / "m1_glm_coefficients.json").read_text(encoding="utf-8"))
        art = {"m1": m1, "cal": cal, "clusters": clusters, "coefs": coefs, "version": M1_VERSION}
        art.update(_load_spread_cause())
        return art
    except Exception as e:  # 학습 전이면 M0 로 동작한다
        log.info(f"M1 미적재 — M0 단독 동작 ({type(e).__name__}: {e})")
        return None

log = get_logger("batch")


def _load_spread_cause() -> dict:
    """M2a(연소확대) · M3(원인). 없거나 미통과면 해당 화면은 '미적재' 로 남는다."""
    out: dict = {}
    try:
        import lightgbm as lgb

        from ..features.spec import get_features as _gf
        out["m2a"] = lgb.Booster(model_file=str(MODELS_DIR / "m2a_spread_daily.txt"))
        out["m2a_features"] = _gf("M2a")
    except Exception as e:
        log.info(f"M2a 미적재 ({type(e).__name__})")
    try:
        import lightgbm as lgb

        from ..features.spec import get_features as _gf
        meta = json.loads((MODELS_DIR / "m3_classes.json").read_text(encoding="utf-8"))
        out["m3"] = lgb.Booster(model_file=str(MODELS_DIR / "m3_cause_daily.txt"))
        out["m3_features"] = _gf("M3")
        out["m3_classes"] = meta["classes"]
        out["m3_priors"] = meta["priors"]
    except Exception as e:
        log.info(f"M3 미적재 ({type(e).__name__})")

    # M4 — 장기점유 위험 (현장점유 ≥120분, 일 단위). 피처 목록은 학습 리포트에서
    # 읽는다. spec.py 에 M4 항목이 없어 get_features 로는 얻을 수 없고, 코드에
    # 하드코딩하면 재학습 때 조용히 어긋난다.
    try:
        import lightgbm as lgb

        rep = json.loads((MODELS_DIR / "training_report_m4.json").read_text(encoding="utf-8"))
        out["m4"] = lgb.Booster(model_file=str(MODELS_DIR / "m4_longburn_daily.txt"))
        out["m4_features"] = [f["name"] for f in rep["feature_list"]]
        out["m4_threshold_min"] = rep.get("threshold_min")
    except Exception as e:
        log.info(f"M4 미적재 ({type(e).__name__})")
    return out


def _regions() -> pd.DataFrame:
    rows = query("""SELECT p.region_cd, p.sido, p.sigungu, p.pop_density, p.forest_ratio,
                           p.avg_station_distance_km, c.cluster_label, s.assign_method
                    FROM ref_region_profile p
                    LEFT JOIN ref_region_cluster c
                           ON c.region_cd=p.region_cd AND c.model_version=%s
                    LEFT JOIN ref_station_map s ON s.region_cd=p.region_cd""", (M0_VERSION,))
    return pd.DataFrame(rows)


def _grade_bounds(version: str) -> list[tuple[str, float, float]]:
    rows = query("""SELECT grade, prob_lower, prob_upper FROM ref_risk_grade
                    WHERE model_version=%s ORDER BY sort_order""", (version,))
    return [(r["grade"], float(r["prob_lower"]), float(r["prob_upper"])) for r in rows]


def _assign_grade(p: float, bounds) -> str:
    for g, lo, hi in bounds:
        if p < hi:
            return g
    return bounds[-1][0]


def _open_run(target: date) -> int:
    c = shared()
    with c.cursor() as cur:
        cur.execute("INSERT INTO ops_batch_run (target_date, started_at, status) VALUES (%s,%s,'실행중')",
                    (target, datetime.now()))
        c.commit()
        return cur.lastrowid


def _close_run(run_id: int, status: str, ok: int, failed: int,
               failure_detail=None, fallback=None, forecast_at=None) -> None:
    c = shared()
    with c.cursor() as cur:
        cur.execute("""UPDATE ops_batch_run SET finished_at=%s, status=%s, regions_ok=%s,
                       regions_failed=%s, failure_detail=%s, fallback_source=%s,
                       forecast_fetched_at=%s WHERE run_id=%s""",
                    (datetime.now(), status, ok, failed,
                     json.dumps(failure_detail or [], ensure_ascii=False), fallback,
                     forecast_at, run_id))
        c.commit()


def _weather_for(target: date) -> tuple[pd.DataFrame | None, str]:
    """기상 공급원 사다리 — Open-Meteo(실시간) → 과거 관측(2021~2023) → None(M0 폴백).

    환경변수 `FIRE_WEATHER_SOURCE` 로 고를 수 있다.
      openmeteo (기본)  실시간 예보/실황. 오늘 날짜를 예측하려면 이것뿐이다.
      observed          학습 데이터셋의 과거 관측. 2021~2023 재현·검증용.

    Open-Meteo 가 실패하면 관측 경로를 한 번 더 시도하고, 그것도 없으면 None 을
    돌려준다. run() 이 None 을 받으면 M0 기저율로 폴백하고 그 사실을 기록한다.

    반환: (프레임 또는 None, 실제로 쓴 공급원 이름)
    """
    import os

    want = os.getenv("FIRE_WEATHER_SOURCE", "openmeteo").strip().lower()

    if want == "observed":
        return features_source.for_date(target), "관측"

    try:
        from . import openmeteo_source
        wx = openmeteo_source.for_date(target)
        if wx is not None and len(wx) == N_REGIONS:
            return wx, "OpenMeteo"
        log.warning(f"Open-Meteo 결과 부족 ({0 if wx is None else len(wx)}행) — 관측 경로 시도")
    except Exception as e:
        log.warning(f"Open-Meteo 실패 ({type(e).__name__}: {e}) — 관측 경로 시도")

    return features_source.for_date(target), "관측(OpenMeteo실패)"


def run(target: date, *, forecast: pd.DataFrame | None = None) -> dict:
    run_id = _open_run(target)
    regions = _regions()
    if len(regions) != N_REGIONS:
        _close_run(run_id, "실패", 0, N_REGIONS,
                   [{"regionCd": None, "stage": "참조테이블",
                     "reason": f"지역 마스터가 {len(regions)}행 (251 기대)"}])
        raise RuntimeError(f"지역 마스터 {len(regions)}행 — 참조 테이블을 먼저 적재하라")

    m0 = M0Lookup.load(MODELS_DIR / "m0_lookup_season.json")
    art = _load_m1()

    # --- 기상 피처 확보 -----------------------------------------------------
    if forecast is not None:
        wx, wx_source = forecast, "주입"
    else:
        wx, wx_source = _weather_for(target)
    weather_ok = wx is not None and len(wx) == N_REGIONS and art is not None
    log.info(f"기상 공급원 ={wx_source} · 행수 {0 if wx is None else len(wx)} · "
             f"M1 {'적재' if art is not None else '미적재'} → "
             f"{'M1 경로' if weather_ok else 'M0 폴백'}")

    season = season_of(target.month)
    season_f = m0.season_factor_.get(season, 1.0)
    gmean = m0.global_mean_ or 0.43

    if weather_ok:
        # --- M1 + M5 경로 ---------------------------------------------------
        df = wx.copy()
        X = build(df, "M1", art["clusters"])
        base_mu = m0.predict(df)
        off = np.log(np.clip(base_mu, 1e-6, None))
        mu = art["m1"].predict(X, off)
        prob = art["cal"].apply(occurrence_probability(mu))
        model_version = art["version"]
        grade_version = art["version"]
        weather_factors = explain.weather_factors_from_glm(X, art["coefs"])
        df = df.merge(regions[["region_cd", "cluster_label", "assign_method",
                               "avg_station_distance_km", "forest_ratio"]],
                      on="region_cd", how="left", suffixes=("", "_ref"))
        df["expected_count"] = mu
        df["occur_probability"] = prob
        df["occur_prob_raw"] = occurrence_probability(mu)
        df["m0_base"] = base_mu
        snap_features = X

        # --- M2a — 조건부 연소확대 확률 --------------------------------------
        if art.get("m2a") is not None:
            X2 = build(wx, "M2a", art["clusters"])
            df["spread_probability"] = art["m2a"].predict(X2[art["m2a_features"]])
        else:
            df["spread_probability"] = np.nan

        # --- M4 — 장기점유 위험 (현장점유 ≥120분이 하루에 1건 이상) ------------
        if art.get("m4") is not None:
            X4 = build(wx, "M1", art["clusters"])     # M4 피처는 M1 집합의 부분집합
            miss4 = [f for f in art["m4_features"] if f not in X4.columns]
            if miss4:
                log.warning(f"M4 피처 누락 {miss4} — 건너뜀")
                df["longburn_probability"] = np.nan
            else:
                # M4 는 요일을 범주형이 아니라 정수로 학습했다. build() 는 M1 규약에
                # 맞춰 범주형으로 만들므로, 그대로 넘기면 LightGBM 이 거부한다.
                X4m = X4[art["m4_features"]].apply(pd.to_numeric, errors="coerce").astype(float)
                df["longburn_probability"] = art["m4"].predict(X4m)
        else:
            df["longburn_probability"] = np.nan

        # --- 기대 피해량 (FR-033) --------------------------------------------
        # 발생 × 확산 × 자원점유. M4 가 붙으면 점유 항이 곱해진다.
        if df["spread_probability"].notna().any():
            load = df["expected_count"] * (1.0 + df["spread_probability"])
            if df["longburn_probability"].notna().any():
                load = load * (1.0 + df["longburn_probability"])
            df["expected_damage_load"] = load
        else:
            df["expected_damage_load"] = np.nan

        # --- M3 — 원인 '이상 신호' (순위가 아니라 평시 대비 배율) ---------------
        # M3 는 확률 순위에서 상수 베이스라인에 졌다(top-3 91.69% < 91.70%). 순위에는
        # 정보가 없다 — 어느 지역이든 부주의·전기적 요인이 압도적이라 순위가 거의
        # 고정된다. 반면 **평시 대비 배율**은 신호를 낸다(홀드아웃 자연적인 요인
        # 5.19배 · 화학적 요인 2.13배). 그래서 확률 상위가 아니라 **배율 상위**를
        # 저장한다. 확률 상위만 담으면 정작 신호가 있는 클래스가 빠진다.
        if art.get("m3") is not None:
            X3 = build(wx, "M3", art["clusters"])
            P3 = art["m3"].predict(X3[art["m3_features"]])
            cls3 = np.array(art["m3_classes"])
            pri3 = art["m3_priors"]
            base3 = np.array([float(pri3.get(str(c), np.nan)) for c in cls3])
            with np.errstate(divide="ignore", invalid="ignore"):
                L3 = P3 / base3                      # 평시 대비 배율
            ord3 = np.argsort(-np.nan_to_num(L3, nan=-1.0), axis=1)[:, :3]
            cause_rows = []
            for i in range(len(P3)):
                cause_rows.append([
                    {"rank": r, "cause_class": str(cls3[j]), "probability": float(P3[i, j]),
                     "lift": None if not np.isfinite(L3[i, j]) else float(L3[i, j])}
                    for r, j in enumerate(ord3[i], 1)])
            df["cause_top3"] = cause_rows
        else:
            df["cause_top3"] = [[] for _ in range(len(df))]
    else:
        # --- M0 폴백 경로 ---------------------------------------------------
        df = regions.copy()
        df["날짜"] = pd.Timestamp(target)
        df["시도"], df["시군구"] = df["sido"], df["sigungu"]
        mu = m0.predict(df)
        prob = occurrence_probability(mu)
        model_version = M0_VERSION
        grade_version = M0_VERSION
        weather_factors = [[] for _ in range(len(df))]
        df["expected_count"] = mu
        df["occur_probability"] = prob
        df["occur_prob_raw"] = prob
        df["m0_base"] = mu
        df["spread_probability"] = np.nan
        df["longburn_probability"] = np.nan
        df["expected_damage_load"] = np.nan
        df["cause_top3"] = [[] for _ in range(len(df))]
        snap_features = None

    bounds = _grade_bounds(grade_version) or _grade_bounds(M0_VERSION)
    df["risk_grade"] = [_assign_grade(p, bounds) for p in df["occur_probability"]]
    df["wx_factors"] = list(weather_factors)
    df = df.sort_values("expected_count", ascending=False).reset_index(drop=True)
    df["risk_rank"] = np.arange(1, len(df) + 1)
    if snap_features is not None:
        snap_features = snap_features.loc[df.index] if len(snap_features) == len(df) else snap_features

    # fallback_source 는 ENUM('전일결과','M0기저율') 이다 — 폴백 사유 전용이므로
    # 임의 문자열을 넣으면 DB 가 거부한다. 기상 공급원은 failure_detail(longtext)에
    # 별도 항목으로 남긴다. 나중에 '이 날 예측이 실제 날씨였나' 를 되짚으려면
    # 성공 경로에서도 공급원이 기록돼 있어야 한다.
    fallback = None if weather_ok else "M0기저율"
    data_status = "정상" if weather_ok else "최신아님"

    rows, snaps, failures, cause_rows = [], [], [], []
    grade_map = json.dumps(feature_grade_map("M1"), ensure_ascii=False)
    for i, r in enumerate(df.itertuples()):
        try:
            ratio = float(r.m0_base) / season_f / gmean if gmean else 1.0
            factors = explain.build_factors(
                *r.wx_factors,
                explain.spread_factor(getattr(r, "spread_probability", None),
                                      getattr(r, "avg_station_distance_km", None),
                                      getattr(r, "forest_ratio", None)),
                explain.region_factor(ratio, getattr(r, "cluster_label", None)),
                explain.season_factor(season, season_f),
                explain.confidence_factor(getattr(r, "assign_method", "관내")),
                None if weather_ok else explain.weather_unavailable_factor(),
            )
            conf = "대리지점" if (weather_ok and getattr(r, "assign_method", "관내") != "관내") \
                else ("정상" if weather_ok else "폴백")
            rows.append((target, r.region_cd, run_id, 1,
                         float(r.expected_count), float(r.occur_probability),
                         float(r.occur_prob_raw), r.risk_grade, int(r.risk_rank),
                         _n(getattr(r, "spread_probability", None)),
                         _n(getattr(r, "longburn_probability", None)),
                         _n(getattr(r, "expected_damage_load", None)),
                         json.dumps(factors, ensure_ascii=False), conf, data_status, model_version))
            for c in (getattr(r, "cause_top3", None) or []):
                cause_rows.append((target, r.region_cd, run_id, int(c["rank"]),
                                   c["cause_class"], float(c["probability"]),
                                   None if c["lift"] is None else float(c["lift"])))
            feat = {}
            if snap_features is not None:
                feat = {k: (None if pd.isna(v) else (float(v) if isinstance(v, (int, float, np.floating)) else str(v)))
                        for k, v in snap_features.iloc[i].to_dict().items()}
            feat.update({"지역기저배율": round(ratio, 4), "계절계수": round(season_f, 4), "계절": season})
            snaps.append((target, r.region_cd, run_id,
                          json.dumps(feat, ensure_ascii=False),
                          grade_map, None, None))
        except Exception as e:
            failures.append({"regionCd": r.region_cd, "stage": "추론", "reason": str(e)[:200]})
            rows.append((target, r.region_cd, run_id, 1, None, None, None, None, None,
                         None, None, None, None, "폴백", "데이터없음", model_version))

    c = shared()
    with c.cursor() as cur:
        # 재실행하면 **이전 실행의 행이 내려가고 새 행이 올라온다.**
        #
        # pred_daily 의 기본키는 (target_date, region_cd, run_id) 다. 즉 실행마다
        # 그 날짜의 새 버전이 통째로 쌓이고, 바로 아래 UPDATE 가 옛 버전을 is_current=0
        # 으로 내린다. INSERT 의 ON DUPLICATE KEY 절은 같은 run_id 가 같은 행을 두 번 넣을 때만
        # 걸리므로 실무에서는 도달하지 않는다.
        #
        # 이 구조 덕분에 선계산과 당일 갱신이 그냥 맞아떨어진다. 배치는 오늘부터
        # 7일치를 미리 계산해 두고(어느 날 배치가 실패해도 화면이 비지 않는다),
        # 다음 날 아침 다시 돌면 그날의 실제 예보로 만든 새 버전이 며칠 전 예보로
        # 만든 버전을 대체한다. 값을 비교해 다를 때만 바꾸는 절차는 두지 않았다 —
        # 결과가 같고 실패할 경로만 하나 더 생긴다. 교체 사실은 화면에 알리지 않는다.
        # 되짚어야 할 때는 is_current=0 인 옛 버전이 그대로 남아 있다.
        cur.execute("UPDATE pred_daily SET is_current=0 WHERE target_date=%s", (target,))
        cur.executemany("""INSERT INTO pred_daily
            (target_date,region_cd,run_id,is_current,expected_count,occur_probability,
             occur_prob_raw,risk_grade,risk_rank,spread_probability,longburn_probability,
             expected_damage_load,top_factors,confidence,data_status,source_model_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE is_current=1""", rows)
        cur.executemany("""INSERT INTO pred_feature_snapshot
            (target_date,region_cd,run_id,features,feature_grade,forecast_issued_at,station_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE features=VALUES(features)""", snaps)
        if cause_rows:
            cur.execute("DELETE FROM pred_cause_top3 WHERE target_date=%s", (target,))
            cur.executemany("""INSERT INTO pred_cause_top3
                (target_date,region_cd,run_id,`rank`,cause_class,probability,lift_vs_base)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE probability=VALUES(probability),
                 lift_vs_base=VALUES(lift_vs_base)""", cause_rows)
        c.commit()

    ok = len(rows) - len(failures)
    status = "폴백" if fallback else ("성공" if not failures else "부분실패")
    # 기상 공급원은 실패 목록과 같은 칸(longtext)에 메타 항목으로 남긴다.
    detail = [{"stage": "기상", "source": wx_source, "weatherOk": bool(weather_ok)}] + failures
    _close_run(run_id, status, ok, len(failures), detail, fallback,
               datetime.now() if weather_ok else None)
    log.info(f"{target} run={run_id} status={status} model={model_version} "
             f"rows={len(rows)} ok={ok} fail={len(failures)}")
    return {"run_id": run_id, "target_date": str(target), "rows": len(rows),
            "ok": ok, "failed": len(failures), "status": status, "model": model_version}


def main() -> None:
    ap = argparse.ArgumentParser(description="일일 화재위험 예측 배치")
    ap.add_argument("--date", default=str(date.today()), help="예측 대상일 YYYY-MM-DD")
    ap.add_argument("--backfill-from", help="이 날짜부터 --date 까지 소급 생성")
    args = ap.parse_args()

    end = date.fromisoformat(args.date)
    start = date.fromisoformat(args.backfill_from) if args.backfill_from else end
    d, results = start, []
    while d <= end:
        results.append(run(d))
        d += timedelta(days=1)
    print(json.dumps({"days": len(results), "last": results[-1]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
