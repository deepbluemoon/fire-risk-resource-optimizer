# -*- coding: utf-8 -*-
"""최종 학습 · 진단 · 적재 아티팩트 생성 (v2).

산출: models/v2/<model_id>/ 에 부스터 + metrics.json
     models/v2/report.json 에 전체 판정표
"""
import sys, json, time, shutil
from pathlib import Path
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.metrics import (average_precision_score, roc_auc_score, mean_absolute_error,
                             top_k_accuracy_score, accuracy_score, brier_score_loss)
from sklearn.isotonic import IsotonicRegression
sys.path.insert(0, str(Path(__file__).resolve().parent))
from opt_common import *

VER = "v2-20260901"
OUTD = ART / "v2"; OUTD.mkdir(parents=True, exist_ok=True)
t0 = time.time(); REPORT = {"version": VER}

print("■ 데이터 · 군집")
D = load(); clusters = canonical_clusters(D["occurrence"]["train"])
occ = D["occurrence"]
dev = add_features(pd.concat([occ["train"], occ["val"]], ignore_index=True), clusters)
ho  = add_features(occ["test"], clusters)
y_ho = ho["화재건수"].values
print(f"  dev {len(dev):,} / holdout {len(ho):,} · 군집 {clusters.nunique()}종")

# ============================================================ M0
print("\n■ M0 — 기저율 룩업")
m0 = M0().fit(dev)
mu0 = m0.predict(ho)
REPORT["M0"] = occ_report("M0 지역x계절", y_ho, mu0)
json.dump({"global_mean": m0.g, "m_smooth": m0.m,
           "region_rate": {k: float(v) for k, v in m0.rate.items()},
           "season_coef": {k: float(v) for k, v in m0.season.items()}},
          open(OUTD / "m0_lookup.json", "w"), ensure_ascii=False, indent=1)

# ============================================================ M1
print("\n■ M1 — 발생 (M0 offset + 상호작용, 5시드)")
FEAT_M1 = ["실효습도","최소습도","무강수일수","강수량","일교차","기온","풍속","연중일","요일","주말",
           "산림율","인구밀도","평균소방서거리","지역유형","전년화재율","전년자료있음","기상배정방식_c",
           "풍속건조지수","건조지속강도","한랭건조","임야건조"]
FEAT_A  = ["실효습도_t1","무강수일수_t1","건조지속강도_t1","연중일","요일","주말",
           "산림율","인구밀도","평균소방서거리","지역유형","전년화재율","전년자료있음","기상배정방식_c"]
CATS = ["요일","지역유형"]
P_M1 = dict(objective="poisson", metric="poisson", learning_rate=0.03, num_leaves=15, max_depth=7,
            min_data_in_leaf=500, feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1,
            lambda_l2=20.0, verbose=-1, num_threads=os.cpu_count(), force_row_wise=True, deterministic=True)

cv1 = cv_poisson(dev, FEAT_M1, CATS, P_M1, offset="m0", tag="M1")
N1 = int(cv1["mean_best_iter"] * 1.15)
mu1, boosters1, _ = fit_predict(dev, ho, FEAT_M1, CATS, P_M1, offset="m0", rounds=N1, n_seeds=5)
REPORT["M1"] = occ_report("M1 (5시드 앙상블)", y_ho, mu1); REPORT["M1"].update(cv1, rounds=N1)
for i, b in enumerate(boosters1): b.save_model(str(OUTD / f"m1_seed{i}.txt"))

cvA = cv_poisson(dev, FEAT_A, CATS, P_M1, offset="m0", tag="M1-A (A등급 전용)")
muA, boostersA, _ = fit_predict(dev, ho, FEAT_A, CATS, P_M1, offset="m0",
                                rounds=int(cvA["mean_best_iter"]*1.15), n_seeds=5)
REPORT["M1-A"] = occ_report("M1-A (예보없음 폴백)", y_ho, muA); REPORT["M1-A"].update(cvA)
for i, b in enumerate(boostersA): b.save_model(str(OUTD / f"m1a_seed{i}.txt"))

# ---- 과적합 정밀 진단 -----------------------------------------------------------
print("\n■ 과적합 진단")
rows = []
for i, f in enumerate(FOLDS, 1):
    mtr, mva = fold_masks(dev, f); dtr, dva = dev[mtr], dev[mva]
    mm = M0().fit(dtr)
    otr = np.log(np.clip(mm.predict(dtr), 1e-6, None)); ova = np.log(np.clip(mm.predict(dva), 1e-6, None))
    ds = lgb.Dataset(dtr[FEAT_M1], label=dtr["화재건수"].values, init_score=otr,
                     categorical_feature=CATS, free_raw_data=False)
    dv = lgb.Dataset(dva[FEAT_M1], label=dva["화재건수"].values, init_score=ova,
                     categorical_feature=CATS, reference=ds, free_raw_data=False)
    b = lgb.train(dict(P_M1, seed=SEED), ds, num_boost_round=4000, valid_sets=[dv],
                  callbacks=[lgb.early_stopping(150, verbose=False)])
    sub = dtr[dtr["월"].isin(set(dva["월"].unique()))]
    has = len(sub) > 0
    pr = lambda x, o: np.exp(b.predict(x[FEAT_M1], raw_score=True) + o)
    osub = np.log(np.clip(mm.predict(sub), 1e-6, None)) if has else None
    rows.append({"fold": i, "train_same_month": pdev(sub["화재건수"], pr(sub, osub)) if has else np.nan,
                 "valid": pdev(dva["화재건수"], pr(dva, ova)),
                 "m0_same_month": pdev(sub["화재건수"], mm.predict(sub)) if has else np.nan,
                 "m0_valid": pdev(dva["화재건수"], mm.predict(dva))})
sg = pd.DataFrame(rows).set_index("fold")
gap_model = float((sg["valid"] - sg["train_same_month"]).mean())
gap_diff  = float((sg["m0_valid"] - sg["m0_same_month"]).mean())
REPORT["overfit"] = {"gap_model_same_month": gap_model, "gap_structural_m0": gap_diff,
                     "excess_gap": gap_model - gap_diff, "raw_gap": cv1["gap"]}
print(f"  계절정합 gap {gap_model:+.4f} − M0 구조적 {gap_diff:+.4f} = 초과 {gap_model-gap_diff:+.4f}")

regs = np.array(sorted(dev["region_cd"].unique())); rng = np.random.RandomState(0); rng.shuffle(regs)
sk = []
for g in np.array_split(regs, 5):
    m = dev["region_cd"].isin(set(g)).values; dtr, dva = dev[~m], dev[m]
    mm = M0().fit(dtr)
    otr = np.log(np.clip(mm.predict(dtr), 1e-6, None)); ova = np.log(np.clip(mm.predict(dva), 1e-6, None))
    b = lgb.train(dict(P_M1, seed=SEED), lgb.Dataset(dtr[FEAT_M1], label=dtr["화재건수"].values,
                  init_score=otr, categorical_feature=CATS, free_raw_data=False), num_boost_round=N1)
    sk.append(pdev(dva["화재건수"], mm.predict(dva))
              - pdev(dva["화재건수"], np.exp(b.predict(dva[FEAT_M1], raw_score=True) + ova)))
REPORT["overfit"]["groupkfold_skill"] = float(np.mean(sk))
REPORT["overfit"]["groupkfold_positive"] = int(sum(1 for x in sk if x > 0))
print(f"  미학습 지역 skill {np.mean(sk):+.4f} ({sum(1 for x in sk if x>0)}/5 폴드 양수)")

sh = dev.copy(); sh["화재건수"] = rng.permutation(sh["화재건수"].values)
a, b_, c, d = [pd.Timestamp(x) for x in FOLDS[-1]]
str_, sva = sh[sh["날짜"].between(a, b_)], sh[sh["날짜"].between(c, d)]
mm = M0().fit(str_)
otr = np.log(np.clip(mm.predict(str_), 1e-6, None)); ova = np.log(np.clip(mm.predict(sva), 1e-6, None))
bs = lgb.train(dict(P_M1, seed=SEED), lgb.Dataset(str_[FEAT_M1], label=str_["화재건수"].values,
               init_score=otr, categorical_feature=CATS, free_raw_data=False),
               num_boost_round=4000, valid_sets=[lgb.Dataset(sva[FEAT_M1], label=sva["화재건수"].values,
               init_score=ova, categorical_feature=CATS, free_raw_data=False)],
               callbacks=[lgb.early_stopping(150, verbose=False)])
REPORT["overfit"]["shuffle_dev"] = pdev(sva["화재건수"], np.exp(bs.predict(sva[FEAT_M1], raw_score=True) + ova))
REPORT["overfit"]["shuffle_const"] = pdev(sva["화재건수"], np.full(len(sva), sh["화재건수"].mean()))
print(f"  라벨 셔플 {REPORT['overfit']['shuffle_dev']:.4f} vs 상수 {REPORT['overfit']['shuffle_const']:.4f}")

# ============================================================ M5 발생확률
print("\n■ M5 — 발생 확률 P(N≥1)  (λ → 1−e^−λ → isotonic)")
oof = np.zeros(len(dev)); seen = np.zeros(len(dev), bool)
for f in FOLDS:
    mtr, mva = fold_masks(dev, f); dtr, dva = dev[mtr], dev[mva]
    mm = M0().fit(dtr)
    otr = np.log(np.clip(mm.predict(dtr), 1e-6, None)); ova = np.log(np.clip(mm.predict(dva), 1e-6, None))
    b = lgb.train(dict(P_M1, seed=SEED), lgb.Dataset(dtr[FEAT_M1], label=dtr["화재건수"].values,
                  init_score=otr, categorical_feature=CATS, free_raw_data=False), num_boost_round=N1)
    oof[mva] = np.exp(b.predict(dva[FEAT_M1], raw_score=True) + ova); seen[mva] = True
p_raw_dev = 1 - np.exp(-oof[seen])
iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(
        p_raw_dev, (dev["화재건수"].values[seen] > 0).astype(float))
p_raw_ho = 1 - np.exp(-mu1); p_cal = iso.predict(p_raw_ho)
occ_ho = (y_ho > 0).astype(float)
REPORT["M5"] = {"brier_raw": float(brier_score_loss(occ_ho, p_raw_ho)),
                "brier_calibrated": float(brier_score_loss(occ_ho, p_cal)),
                "base_rate": float(occ_ho.mean()),
                "brier_const": float(brier_score_loss(occ_ho, np.full(len(occ_ho), occ_ho.mean())))}
print(f"  Brier 원값 {REPORT['M5']['brier_raw']:.5f} → 보정 {REPORT['M5']['brier_calibrated']:.5f} "
      f"(상수 {REPORT['M5']['brier_const']:.5f})")
json.dump({"x": [float(v) for v in iso.X_thresholds_], "y": [float(v) for v in iso.y_thresholds_]},
          open(OUTD / "m5_isotonic.json", "w"), ensure_ascii=False, indent=1)

# ============================================================ 건 단위 모델
print("\n■ M2a · M2b · M4 · M3 (일 단위 피처, 배치 가능)")
def prep(kind):
    d = {s: add_features(D[kind][s], clusters) for s in ("train","val","test")}
    for s in d:
        if "피해액_천원" in d[s]: d[s]["log_피해액"] = np.log1p(d[s]["피해액_천원"].clip(lower=0))
    return {"dev": pd.concat([d["train"], d["val"]], ignore_index=True), "ho": d["test"]}
S, C = prep("spread"), prep("cause")
FEAT_INC = ["실효습도","일최소습도","무강수일수","강수량","일기온","일풍속","월","연중일","요일","주말",
            "산림율","인구밀도","소방서거리","지역유형","풍속건조지수","건조지속강도","한랭건조","임야건조"]
INC_FOLDS = [("2021-01-01","2021-09-30","2021-10-01","2021-12-31"),
             ("2021-01-01","2022-03-31","2022-04-01","2022-06-30"),
             ("2021-01-01","2022-06-30","2022-07-01","2022-12-31")]
P_BIN = dict(objective="binary", metric="average_precision", learning_rate=0.04, num_leaves=31,
             max_depth=7, min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
             bagging_freq=1, lambda_l2=5.0, verbose=-1, num_threads=os.cpu_count(),
             force_row_wise=True, deterministic=True)

def fit_inc(dev_, ho_, target, params, name, n_seeds=3, feats=FEAT_INC):
    iters = []
    for a, b, c, d in INC_FOLDS:
        mtr = dev_["날짜"].between(pd.Timestamp(a), pd.Timestamp(b)).values
        mva = dev_["날짜"].between(pd.Timestamp(c), pd.Timestamp(d)).values
        ds = lgb.Dataset(dev_.loc[mtr, feats], label=dev_.loc[mtr, target].values,
                         categorical_feature=CATS, free_raw_data=False)
        dv = lgb.Dataset(dev_.loc[mva, feats], label=dev_.loc[mva, target].values,
                         categorical_feature=CATS, reference=ds, free_raw_data=False)
        iters.append(lgb.train(dict(params, seed=SEED), ds, num_boost_round=3000, valid_sets=[dv],
                     callbacks=[lgb.early_stopping(100, verbose=False)]).best_iteration)
    n = max(20, int(np.mean(iters) * 1.15)); preds, ms = [], []
    for s in range(n_seeds):
        m = lgb.train(dict(params, seed=SEED+s*101, bagging_seed=SEED+s*101, feature_fraction_seed=SEED+s*101),
                      lgb.Dataset(dev_[feats], label=dev_[target].values, categorical_feature=CATS,
                      free_raw_data=False), num_boost_round=n)
        ms.append(m); preds.append(m.predict(ho_[feats]))
        m.save_model(str(OUTD / f"{name}_seed{s}.txt"))
    return np.mean(preds, axis=0), ms, n

p2a, _, n2a = fit_inc(S["dev"], S["ho"], "연소확대", P_BIN, "m2a")
y2a = S["ho"]["연소확대"].values
REPORT["M2a"] = {"PR_AUC": float(average_precision_score(y2a, p2a)), "ROC_AUC": float(roc_auc_score(y2a, p2a)),
                 "base_rate": float(y2a.mean()), "rounds": n2a}
REPORT["M2a"]["lift"] = REPORT["M2a"]["PR_AUC"] / REPORT["M2a"]["base_rate"]
print(f"  M2a PR-AUC {REPORT['M2a']['PR_AUC']:.4f} (기저 {y2a.mean():.4f}, lift {REPORT['M2a']['lift']:.2f})")

def boot_recall(y_off, score, frac=.10, B=2000, seed=0):
    rng2 = np.random.RandomState(seed); n = len(y_off); k = int(n*frac)
    top = np.zeros(n, bool); top[np.argsort(-score, kind="mergesort")[:k]] = True
    pos = np.where(y_off == 1)[0]
    v = [top[rng2.choice(pos, len(pos), replace=True)].mean() for _ in range(B)]
    return float(top[pos].mean()), float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))
m4rows = {}
for t in ["대형화재_진화120","대형화재_진화60","대형화재_인명","대형화재_재산"]:
    p, _, n = fit_inc(S["dev"], S["ho"], t, P_BIN, f"m4_{t}")
    yv = S["ho"][t].values; pt, lo, hi = boot_recall(S["ho"]["공식대형"].values, p)
    m4rows[t] = {"양성률": float(yv.mean()), "PR_AUC": float(average_precision_score(yv, p)),
                 "lift": float(average_precision_score(yv, p)/yv.mean()),
                 "공식대형r@10": pt, "CI_lo": lo, "CI_hi": hi, "rounds": n}
REPORT["M4"] = m4rows
print("  M4:", {k: f"lift {v['lift']:.2f} · CI[{v['CI_lo']*100:.0f}~{v['CI_hi']*100:.0f}%]" for k, v in m4rows.items()})

S["dev"]["피해있음"] = (S["dev"]["피해액_천원"] > 0).astype(int)
p_pos, _, _ = fit_inc(S["dev"], S["ho"], "피해있음", P_BIN, "m2b_p0")
pos = S["dev"][S["dev"]["피해액_천원"] > 0].reset_index(drop=True)
P_L1 = dict(P_BIN); P_L1.update(objective="regression_l1", metric="l1")
mu_l1, _, _ = fit_inc(pos, S["ho"], "log_피해액", P_L1, "m2b_med")
P_L2 = dict(P_L1); P_L2.update(objective="regression", metric="l2")
mu_l2, m2b_mean_models, _ = fit_inc(pos, S["ho"], "log_피해액", P_L2, "m2b_mean")
resid = pos["log_피해액"].values - np.mean([m.predict(pos[FEAT_INC]) for m in m2b_mean_models], axis=0)
smear = float(np.mean(np.exp(resid)))
y2b = S["ho"]["피해액_천원"].values
p_rank = p_pos * np.expm1(mu_l1)
p_total = np.clip(p_pos * (np.exp(mu_l2) * smear - 1), 0, None)
base = np.full_like(y2b, np.median(S["dev"]["피해액_천원"]), dtype=float)
REPORT["M2b"] = {"smearing": smear,
    "rank_logMAE": float(mean_absolute_error(np.log1p(y2b), np.log1p(p_rank))),
    "base_logMAE": float(mean_absolute_error(np.log1p(y2b), np.log1p(base))),
    "rank_spearman": float(pd.Series(p_rank).corr(pd.Series(y2b), method="spearman")),
    "total_ratio": float(p_total.sum()/y2b.sum())}
REPORT["M2b"]["logMAE_improve_%"] = (REPORT["M2b"]["base_logMAE"] - REPORT["M2b"]["rank_logMAE"]) / REPORT["M2b"]["base_logMAE"] * 100
print(f"  M2b 랭킹 로그MAE {REPORT['M2b']['rank_logMAE']:.4f} (기준 {REPORT['M2b']['base_logMAE']:.4f}, "
      f"{REPORT['M2b']['logMAE_improve_%']:+.1f}%) · 총액비 {REPORT['M2b']['total_ratio']:.3f}")

CLASSES = sorted(C["dev"]["원인"].unique()); CMAP = {c: i for i, c in enumerate(CLASSES)}
for k in ("dev","ho"): C[k]["y"] = C[k]["원인"].map(CMAP).astype(int)
P_MC = dict(objective="multiclass", num_class=len(CLASSES), metric="multi_logloss", learning_rate=0.05,
            num_leaves=31, max_depth=7, min_data_in_leaf=100, feature_fraction=0.8, bagging_fraction=0.8,
            bagging_freq=1, lambda_l2=5.0, verbose=-1, num_threads=os.cpu_count(),
            force_row_wise=True, deterministic=True)
p3, _, n3 = fit_inc(C["dev"], C["ho"], "y", P_MC, "m3")
y3 = C["ho"]["y"].values
top3c = C["dev"]["y"].value_counts().index[:3].tolist()
const3 = float(np.isin(y3, top3c).mean()); mod3 = float(top_k_accuracy_score(y3, p3, k=3, labels=np.arange(len(CLASSES))))
rank3 = np.argsort(-p3, axis=1)[:, :3]
lifts = {}
for c, i in CMAP.items():
    inc = (rank3 == i).any(axis=1); br = float((y3 == i).mean())
    lifts[c] = float((y3[inc] == i).mean()/br) if inc.sum() and br else None
REPORT["M3"] = {"top1": float(accuracy_score(y3, p3.argmax(1))), "top3": mod3, "const_top3": const3,
                "margin": mod3 - const3, "lifts": lifts, "classes": CLASSES, "rounds": n3}
print(f"  M3 top-3 {mod3:.4f} vs 상수 {const3:.4f} (마진 {mod3-const3:+.4f})")

# ============================================================ 판정
print("\n■ 합격 판정 (answer.md 2주차 갱신 기준)")
m1 = REPORT["M1"]
checks = [
 ("M1 holdout deviance < 0.9100", m1["deviance"] < 0.9100, f"{m1['deviance']:.4f}"),
 ("M1 캘리브레이션 0.95~1.05", 0.95 <= m1["calib"] <= 1.05, f"{m1['calib']:.3f}"),
 ("M1 recall@20% ≥ λ상한×0.97", m1["상한대비"] >= 0.97, f"{m1['상한대비']*100:.1f}% (λ상한 {m1['ceil@20']*100:.1f}%)"),
 ("M1 초과 gap ≤ +0.02", REPORT["overfit"]["excess_gap"] <= 0.02, f"{REPORT['overfit']['excess_gap']:+.4f}"),
 ("M1 미학습지역 skill > 0", REPORT["overfit"]["groupkfold_skill"] > 0, f"{REPORT['overfit']['groupkfold_skill']:+.4f}"),
 ("M1 라벨셔플 ≥ 상수", REPORT["overfit"]["shuffle_dev"] >= REPORT["overfit"]["shuffle_const"] - 0.005, "정상"),
 ("M5 Brier < 상수", REPORT["M5"]["brier_calibrated"] < REPORT["M5"]["brier_const"], f"{REPORT['M5']['brier_calibrated']:.5f}"),
 ("M2a PR-AUC > 기저×1.5", REPORT["M2a"]["lift"] > 1.5, f"lift {REPORT['M2a']['lift']:.2f}"),
 ("M2b 로그MAE 개선", REPORT["M2b"]["logMAE_improve_%"] > 0, f"{REPORT['M2b']['logMAE_improve_%']:+.1f}%"),
 ("M4 공식대형 CI하한 > 40%", m4rows["대형화재_진화120"]["CI_lo"] > 0.40, f"CI하한 {m4rows['대형화재_진화120']['CI_lo']*100:.1f}%"),
 ("M3 top-3 > 상수+1%p", REPORT["M3"]["margin"] > 0.01, f"마진 {REPORT['M3']['margin']:+.4f}"),
]
verdict = pd.DataFrame([{"기준": a, "판정": "PASS" if b else "FAIL", "실측": c} for a, b, c in checks])
print(verdict.to_string(index=False))
REPORT["verdict"] = verdict.to_dict("records")
REPORT["features"] = {"M1": FEAT_M1, "M1-A": FEAT_A, "incident": FEAT_INC, "cats": CATS,
                      "M3_classes": CLASSES}
REPORT["clusters"] = {k: int(v) for k, v in clusters.items()}
json.dump(REPORT, open(OUTD / "report.json", "w"), ensure_ascii=False, indent=1, default=float)
print(f"\n저장: {OUTD}  ({time.time()-t0:.0f}s)")
