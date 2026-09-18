# -*- coding: utf-8 -*-
"""모델 최적화 공용 하네스.

원칙 (answer.md §0)
  - 모든 선택은 dev(2021-01~2022-12) CV 로만 한다. test(2023)는 최종 1회.
  - 군집은 DB `ref_region_cluster` 를 정본으로 읽는다 (P-10).
  - 키는 `fire_db.region_cd` (공백 유지) 하나만 쓴다 (P-01).
  - `실효습도`·`무강수일수` 는 B등급, `_t1` 변종이 A등급이다 (P-06).
"""
from __future__ import annotations
import os, sys, json, math, time
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_poisson_deviance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fire_db

CACHE = ROOT / "data" / "cache"
ART = ROOT / "models"
SEED = 42

DEV_START, DEV_END = "2021-01-01", "2022-12-31"
HO_START, HO_END = "2023-01-01", "2023-12-31"

FOLDS = [("2021-01-01", "2021-09-30", "2021-10-01", "2021-12-31"),
         ("2021-01-01", "2021-12-31", "2022-01-01", "2022-03-31"),
         ("2021-01-01", "2022-03-31", "2022-04-01", "2022-06-30"),
         ("2021-01-01", "2022-06-30", "2022-07-01", "2022-09-30"),
         ("2021-01-01", "2022-09-30", "2022-10-01", "2022-12-31")]

# ---------------------------------------------------------------- 데이터
def load(refresh=False):
    d = fire_db.load(cache_dir=CACHE, refresh=refresh, verbose=False)
    for kind in ("occurrence", "spread", "cause"):
        for s in d[kind]:
            df = d[kind][s]
            df["날짜"] = pd.to_datetime(df["날짜"])
            df["region_cd"] = fire_db.region_cd(df["시도"], df["시군구"])
            m = df["월"] if "월" in df else df["날짜"].dt.month
            df["계절"] = np.select([m.isin([3,4,5]), m.isin([6,7,8]), m.isin([9,10,11])],
                                  ["봄","여름","가을"], default="겨울")
    return d

def canonical_clusters(fallback_df=None) -> pd.Series:
    """DB ref_region_cluster 를 정본으로 읽는다. 실패하면 train 구간에서 재적합."""
    try:
        import pymysql
        with pymysql.connect(**fire_db.DB_DEFAULT) as c:
            t = pd.read_sql("SELECT region_cd, cluster_id, cluster_label FROM ref_region_cluster", c)
        if len(t) == 251:
            return t.set_index("region_cd")["cluster_id"].astype(int)
    except Exception as e:
        print("  ref_region_cluster 읽기 실패 -> 재적합:", type(e).__name__)
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    p = fallback_df.copy()
    p["log_면적"] = np.log(p["면적_km2"].clip(lower=1e-3))
    g = p.groupby("region_cd")[["log_인구","인구밀도","산림율","log_면적","평균소방서거리"]].median()
    lab = KMeans(6, n_init=50, random_state=SEED).fit(StandardScaler().fit_transform(g.values)).labels_
    return pd.Series(lab, index=g.index, name="cluster_id")

# ---------------------------------------------------------------- 피처
INTERACTIONS = ["풍속건조지수", "건조지속강도", "한랭건조", "임야건조",
                "풍속건조지수_t1", "건조지속강도_t1"]

def add_features(df: pd.DataFrame, clusters: pd.Series) -> pd.DataFrame:
    out = df.copy()
    # 건 단위 데이터셋에는 없는 시간 파생을 날짜에서 만든다
    if "월" not in out:    out["월"] = out["날짜"].dt.month
    if "연중일" not in out: out["연중일"] = out["날짜"].dt.dayofyear
    out["지역유형"] = out["region_cd"].map(clusters).fillna(-1).astype(int)
    out["전년자료있음"] = out["전년화재율"].notna().astype("int8") if "전년화재율" in out else 0
    out["기상배정방식_c"] = (out["기상배정방식"] == "시도평균").astype("int8") if "기상배정방식" in out else 0

    def col(*c):
        for x in c:
            if x in out: return pd.to_numeric(out[x], errors="coerce")
        return pd.Series(np.nan, index=out.index)

    eh   = col("실효습도").clip(0, 100);      dry = (100.0 - eh) / 100.0
    eh1  = col("실효습도_t1").clip(0, 100);   dry1 = (100.0 - eh1) / 100.0
    wind = col("풍속", "일풍속")
    nod  = col("무강수일수").clip(lower=0);   nod1 = col("무강수일수_t1").clip(lower=0)
    temp = col("기온", "일기온");             forest = col("산림율")

    out["풍속건조지수"]   = wind * dry
    out["건조지속강도"]   = np.sqrt(nod) * dry
    out["한랭건조"]      = (1.0 - temp / 30.0).clip(-1, 2) * dry
    out["임야건조"]      = (forest / 100.0) * dry
    out["풍속건조지수_t1"] = wind * dry1
    out["건조지속강도_t1"] = np.sqrt(nod1) * dry1
    return out

# 관측가능성 등급 (P-06 정정본)
GRADE = {
    # A 과거확정 — 당일 관측이 들어가지 않는다
    **{k: "A" for k in ["실효습도_t1","무강수일수_t1","전년화재율","전년자료있음","산림율",
                        "인구밀도","평균소방서거리","소방서거리","log_인구","지역유형",
                        "기상배정방식_c","연중일","월","요일","주말","건조지속강도_t1"]},
    # B 예보의존 — 당일 값이 필요하다
    **{k: "B" for k in ["실효습도","무강수일수","기온","최저기온","최고기온","일교차","풍속",
                        "강수량","최소습도","습도","일기온","일습도","일최소습도","일풍속",
                        "풍속건조지수","건조지속강도","한랭건조","임야건조","풍속건조지수_t1"]},
}

def grades(feats):  # 진단용
    return {f: GRADE.get(f, "?") for f in feats}

# ---------------------------------------------------------------- M0
class M0:
    """지역평균(스무딩 m) × 전역 계절계수."""
    def __init__(self, m=20): self.m = m
    def fit(self, df):
        self.g = float(df["화재건수"].mean())
        a = df.groupby("region_cd")["화재건수"].agg(["sum", "count"])
        self.rate = (a["sum"] + self.m * self.g) / (a["count"] + self.m)
        self.season = df.groupby("계절")["화재건수"].mean() / self.g
        return self
    def predict(self, df):
        return (df["region_cd"].map(self.rate).fillna(self.g).values
                * df["계절"].map(self.season).fillna(1.0).values)

# ---------------------------------------------------------------- 지표
def pdev(y, mu): return float(mean_poisson_deviance(np.asarray(y, float), np.clip(np.asarray(mu, float), 1e-9, None)))
def calib(y, mu): return float(np.sum(mu) / np.sum(y))
def recall_at(y, s, f):
    y = np.asarray(y, float); n = max(1, int(round(len(y) * f)))
    return float(y[np.argsort(-np.asarray(s, float), kind="mergesort")[:n]].sum() / y.sum())
def lam_ceiling(mu, f):
    """모델이 자기 λ 로 완벽히 랭킹했을 때의 recall@f 기댓값 상한 (P-05)."""
    mu = np.asarray(mu, float); n = int(len(mu) * f)
    return float(np.sort(mu)[::-1][:n].sum() / mu.sum())

def occ_report(name, y, mu, extra=None):
    r = {"deviance": pdev(y, mu), "calib": calib(y, mu),
         "recall@20": recall_at(y, mu, .20), "ceil@20": lam_ceiling(mu, .20),
         "recall@10": recall_at(y, mu, .10)}
    r["상한대비"] = r["recall@20"] / r["ceil@20"]
    if extra: r.update(extra)
    print(f"  {name:28s} dev={r['deviance']:.4f} calib={r['calib']:.3f} "
          f"r@20={r['recall@20']*100:.1f}% (상한 {r['ceil@20']*100:.1f}%, {r['상한대비']*100:.1f}%)")
    return r

# ---------------------------------------------------------------- CV
def fold_masks(df, f):
    a, b, c, d = [pd.Timestamp(x) for x in f]
    return df["날짜"].between(a, b).values, df["날짜"].between(c, d).values

def cv_poisson(dev, feats, cats, params, *, offset="m0", rounds=4000, esr=150,
               n_seeds=1, verbose=False, tag=""):
    """Expanding-window 5폴드. offset='m0' | 'pop'.

    반환: cv_valid(폴드 평균 deviance), gap, excess_gap, skill, mean_best_iter
    """
    tr_d, va_d, base_d, iters = [], [], [], []
    for i, f in enumerate(FOLDS, 1):
        mtr, mva = fold_masks(dev, f)
        dtr, dva = dev[mtr], dev[mva]
        m0 = M0().fit(dtr)
        if offset == "m0":
            otr, ova = np.log(np.clip(m0.predict(dtr), 1e-6, None)), np.log(np.clip(m0.predict(dva), 1e-6, None))
        else:
            r0 = dtr["화재건수"].sum() / np.exp(dtr["log_인구"]).sum()
            otr, ova = dtr["log_인구"].values + np.log(r0), dva["log_인구"].values + np.log(r0)
        mu_tr = np.zeros(len(dtr)); mu_va = np.zeros(len(dva))
        for s in range(n_seeds):
            p = dict(params); p.update(seed=SEED + s * 101, bagging_seed=SEED + s * 101,
                                       feature_fraction_seed=SEED + s * 101)
            ds = lgb.Dataset(dtr[feats], label=dtr["화재건수"].values, init_score=otr,
                             categorical_feature=[c for c in cats if c in feats], free_raw_data=False)
            dv = lgb.Dataset(dva[feats], label=dva["화재건수"].values, init_score=ova,
                             categorical_feature=[c for c in cats if c in feats], reference=ds, free_raw_data=False)
            b = lgb.train(p, ds, num_boost_round=rounds, valid_sets=[dv],
                          callbacks=[lgb.early_stopping(esr, verbose=False)])
            iters.append(b.best_iteration)
            mu_tr += np.exp(b.predict(dtr[feats], raw_score=True) + otr) / n_seeds
            mu_va += np.exp(b.predict(dva[feats], raw_score=True) + ova) / n_seeds
        tr_d.append(pdev(dtr["화재건수"], mu_tr)); va_d.append(pdev(dva["화재건수"], mu_va))
        base_d.append(pdev(dva["화재건수"], m0.predict(dva)))
        if verbose:
            print(f"    fold{i} iter={iters[-1]:4d} train={tr_d[-1]:.4f} valid={va_d[-1]:.4f} M0={base_d[-1]:.4f}")
    gap = float(np.mean(va_d) - np.mean(tr_d))
    res = dict(cv_valid=float(np.mean(va_d)), cv_train=float(np.mean(tr_d)),
               cv_m0=float(np.mean(base_d)), gap=gap, skill=float(np.mean(base_d) - np.mean(va_d)),
               mean_best_iter=int(np.mean(iters)), fold_valid=[float(v) for v in va_d])
    if tag:
        print(f"  [{tag:34s}] CV valid {res['cv_valid']:.4f}  M0 {res['cv_m0']:.4f}  "
              f"skill {res['skill']:+.4f}  gap {gap:+.4f}  iter {res['mean_best_iter']}")
    return res

def fit_predict(dev, ho, feats, cats, params, *, offset="m0", rounds=1000, n_seeds=1):
    m0 = M0().fit(dev)
    if offset == "m0":
        od, oh = np.log(np.clip(m0.predict(dev), 1e-6, None)), np.log(np.clip(m0.predict(ho), 1e-6, None))
    else:
        r0 = dev["화재건수"].sum() / np.exp(dev["log_인구"]).sum()
        od, oh = dev["log_인구"].values + np.log(r0), ho["log_인구"].values + np.log(r0)
    mu = np.zeros(len(ho)); boosters = []
    for s in range(n_seeds):
        p = dict(params); p.update(seed=SEED + s * 101, bagging_seed=SEED + s * 101,
                                   feature_fraction_seed=SEED + s * 101)
        ds = lgb.Dataset(dev[feats], label=dev["화재건수"].values, init_score=od,
                         categorical_feature=[c for c in cats if c in feats], free_raw_data=False)
        b = lgb.train(p, ds, num_boost_round=rounds)
        boosters.append(b)
        mu += np.exp(b.predict(ho[feats], raw_score=True) + oh) / n_seeds
    return mu, boosters, m0
