# -*- coding: utf-8 -*-
"""M1-A (예보 없는 날의 폴백) — v3 기준 재학습.

왜 필요한가
  M1 의 피처 21개 중 11개가 B등급(당일 예보 의존)이다. 예보가 없으면 M1 을 못 돌린다.
  폴백이 M0 뿐이면 '날씨를 아예 안 보는 지역평균표'까지 추락한다.
      M1 0.8859  ->  M1-A 0.8963  ->  M0 0.9108
  M0->M1-A 개선폭 0.0145 는 M1 이 M0 대비 얻은 전체 이득(0.0249)의 58% 다.

왜 재학습하는가
  models/v2/m1a_seed*.txt 는 v2 offset(지역평균 x 계절계수) 기준이다.
  v3 M1 은 offset 에서 계절계수를 뺐다. 두 모델의 출발점이 다르면 폴백 시 값이 튄다.
  -> M1 과 **같은 offset·같은 2구조 앙상블**로 다시 학습한다.

    python scripts/train_m1a_v3.py
"""
from __future__ import annotations
import os, sys, json, time
from pathlib import Path
import numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0, str(Path(__file__).resolve().parent))
from opt_common import *

t0 = time.time()
OUTD = ART / "v3"; OUTD.mkdir(parents=True, exist_ok=True)

D = load(); clusters = canonical_clusters(D["occurrence"]["train"]); occ = D["occurrence"]
dev = add_features(pd.concat([occ["train"], occ["val"]], ignore_index=True), clusters)
ho  = add_features(occ["test"], clusters)
y_ho = ho["화재건수"].values
print(f"■ dev {len(dev):,} / holdout {len(ho):,}")

# A등급 전용 — 당일 관측이 필요한 피처는 하나도 없어야 한다
FEAT_A = ["실효습도_t1", "무강수일수_t1", "건조지속강도_t1", "연중일", "요일", "주말",
          "산림율", "인구밀도", "평균소방서거리", "지역유형",
          "전년화재율", "전년자료있음", "기상배정방식_c"]
CATS = ["요일", "지역유형"]

g = grades(FEAT_A)
bad = {f: v for f, v in g.items() if v != "A"}
assert not bad, f"A등급이 아닌 피처가 섞였다: {bad}"
print(f"■ 피처 {len(FEAT_A)}개 전부 A등급 확인 ✓")

# v3 M1 과 동일한 구조 (train_final_v3.py)
P    = dict(objective="poisson", metric="poisson", learning_rate=0.03, num_leaves=15, max_depth=7,
            min_data_in_leaf=500, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1,
            lambda_l2=20.0, verbose=-1, num_threads=os.cpu_count(),
            force_row_wise=True, deterministic=True)
DEEP   = dict(P, num_leaves=63, min_data_in_leaf=1000, lambda_l2=50.0)
STRUCT = [("기본", P), ("깊은", DEEP)]


class M0Region:
    """v3 offset — 지역평균만. 계절계수 없음. (M1 과 반드시 같아야 한다)"""
    def __init__(self, m=20): self.m = m
    def fit(self, df):
        self.g = float(df["화재건수"].mean())
        a = df.groupby("region_cd")["화재건수"].agg(["sum", "count"])
        self.rate = (a["sum"] + self.m * self.g) / (a["count"] + self.m); return self
    def predict(self, df): return df["region_cd"].map(self.rate).fillna(self.g).values

def off(m0, df): return np.log(np.clip(m0.predict(df), 1e-6, None))


print("\n■ CV (Expanding-window 5폴드)")
tr, va, bs, it = [], [], [], []
for f in FOLDS:
    mtr, mva = fold_masks(dev, f); dtr, dva = dev[mtr], dev[mva]
    mm = M0Region().fit(dtr); otr, ova = off(mm, dtr), off(mm, dva)
    a = np.zeros(len(dtr)); b = np.zeros(len(dva))
    for _, pp in STRUCT:
        ds = lgb.Dataset(dtr[FEAT_A], label=dtr["화재건수"].values, init_score=otr,
                         categorical_feature=CATS, free_raw_data=False)
        dv = lgb.Dataset(dva[FEAT_A], label=dva["화재건수"].values, init_score=ova,
                         categorical_feature=CATS, reference=ds, free_raw_data=False)
        m = lgb.train(dict(pp, seed=SEED), ds, num_boost_round=4000, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(150, verbose=False)])
        it.append(m.best_iteration)
        a += np.exp(m.predict(dtr[FEAT_A], raw_score=True) + otr) / len(STRUCT)
        b += np.exp(m.predict(dva[FEAT_A], raw_score=True) + ova) / len(STRUCT)
    tr.append(pdev(dtr["화재건수"], a)); va.append(pdev(dva["화재건수"], b))
    bs.append(pdev(dva["화재건수"], mm.predict(dva)))

cv = dict(cv_valid=float(np.mean(va)), cv_train=float(np.mean(tr)), cv_m0=float(np.mean(bs)),
          gap=float(np.mean(va) - np.mean(tr)), skill=float(np.mean(bs) - np.mean(va)),
          mean_best_iter=int(np.mean(it)))
print(f"  CV valid {cv['cv_valid']:.4f}  skill {cv['skill']:+.4f}  gap {cv['gap']:+.4f}")

N = int(cv["mean_best_iter"] * 1.15)
print(f"\n■ 최종 학습 — 2구조 x 5시드 = 10모델 (rounds {N})")
m0r = M0Region().fit(dev); od, oh = off(m0r, dev), off(m0r, ho)
mu = np.zeros(len(ho))
for nm, pp in STRUCT:
    for s in range(5):
        p = dict(pp, seed=SEED + s * 101, bagging_seed=SEED + s * 101,
                 feature_fraction_seed=SEED + s * 101)
        m = lgb.train(p, lgb.Dataset(dev[FEAT_A], label=dev["화재건수"].values, init_score=od,
                      categorical_feature=CATS, free_raw_data=False), num_boost_round=N)
        m.save_model(str(OUTD / f"m1a_{nm}_seed{s}.txt"))
        mu += np.exp(m.predict(ho[FEAT_A], raw_score=True) + oh) / 10

mu0 = m0r.predict(ho)
R = {
    "version": "v3-20260902",
    "features": FEAT_A, "cats": CATS, "rounds": N, "n_models": 10,
    "deviance": pdev(y_ho, mu), "calib": calib(y_ho, mu),
    "recall@20": recall_at(y_ho, mu, 0.20), "ceil@20": lam_ceiling(mu, 0.20),
    "M0_deviance": pdev(y_ho, mu0),
    **cv,
}
R["상한대비"] = R["recall@20"] / R["ceil@20"]

print(f"""
■ 결과 (봉인 2023)
  M1-A holdout deviance   {R['deviance']:.4f}
  M0   holdout deviance   {R['M0_deviance']:.4f}
  캘리브레이션              {R['calib']:.3f}
  recall@20%              {R['recall@20']:.2%}  (상한 대비 {R['상한대비']:.1%})
  CV valid                {R['cv_valid']:.4f}   gap {R['gap']:+.4f}
""")

ok_m0   = R["deviance"] < R["M0_deviance"]
ok_cal  = 0.95 <= R["calib"] <= 1.05
ok_over = R["deviance"] < R["cv_valid"]
print(f"  M0 보다 낫다        {'PASS' if ok_m0 else 'FAIL'}  ({R['deviance']:.4f} < {R['M0_deviance']:.4f})")
print(f"  캘리브레이션 0.95~1.05 {'PASS' if ok_cal else 'FAIL'}  ({R['calib']:.3f})")
print(f"  holdout < CV        {'PASS' if ok_over else 'FAIL'}  (과적합 아님)")
R["verdict"] = {"M0보다우수": bool(ok_m0), "캘리브레이션": bool(ok_cal), "holdout<CV": bool(ok_over)}

(OUTD / "m1a_report.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n저장: {OUTD/'m1a_report.json'} · 모델 10개 ({time.time()-t0:.0f}s)")
