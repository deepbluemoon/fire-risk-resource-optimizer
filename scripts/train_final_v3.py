# -*- coding: utf-8 -*-
"""최종 학습 v3 — 성능 고점 + 과적합/누수 전면 감사.

v2 대비 바뀐 것 (전부 dev CV 로만 선택)
  1. M1 offset 에서 계절계수 제거  : log(M0) -> log(지역평균만)   CV 0.8944 -> 0.8912
  2. 구조 앙상블(기본+깊은 트리)      : CV 0.8912 -> 0.8910
  3. 각 구조 5시드 평균 (분산 감소)
기각된 것: 변화량·이동통계·순환성·공휴일·기상학적계절·단조제약·시군구범주·linear_tree·dart
"""
import sys, json, time
from pathlib import Path
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.metrics import (average_precision_score, roc_auc_score, mean_absolute_error,
                             top_k_accuracy_score, accuracy_score, brier_score_loss)
from sklearn.isotonic import IsotonicRegression
sys.path.insert(0, str(Path(__file__).resolve().parent))
from opt_common import *

VER="v3-20260901"; OUTD=ART/"v3"; OUTD.mkdir(parents=True,exist_ok=True)
t0=time.time(); REP={"version":VER}

D=load(); clusters=canonical_clusters(D["occurrence"]["train"]); occ=D["occurrence"]
dev=add_features(pd.concat([occ["train"],occ["val"]],ignore_index=True), clusters)
ho =add_features(occ["test"], clusters)
y_ho=ho["화재건수"].values
print(f"■ dev {len(dev):,} / holdout {len(ho):,} · 군집 {clusters.nunique()}종")

FEAT=["실효습도","최소습도","무강수일수","강수량","일교차","기온","풍속","연중일","요일","주말",
      "산림율","인구밀도","평균소방서거리","지역유형","전년화재율","전년자료있음","기상배정방식_c",
      "풍속건조지수","건조지속강도","한랭건조","임야건조"]
CATS=["요일","지역유형"]
P    =dict(objective="poisson",metric="poisson",learning_rate=0.03,num_leaves=15,max_depth=7,
           min_data_in_leaf=500,feature_fraction=0.7,bagging_fraction=0.8,bagging_freq=1,
           lambda_l2=20.0,verbose=-1,num_threads=os.cpu_count(),force_row_wise=True,deterministic=True)
DEEP =dict(P,num_leaves=63,min_data_in_leaf=1000,lambda_l2=50.0)
STRUCT=[("기본",P),("깊은",DEEP)]

class M0Region:
    """M1 offset 전용 — 지역평균만. 계절은 모델이 직접 학습하는 편이 낫다(CV 실측)."""
    def __init__(self,m=20): self.m=m
    def fit(self,df):
        self.g=float(df["화재건수"].mean())
        a=df.groupby("region_cd")["화재건수"].agg(["sum","count"])
        self.rate=(a["sum"]+self.m*self.g)/(a["count"]+self.m); return self
    def predict(self,df): return df["region_cd"].map(self.rate).fillna(self.g).values

def off(m0,df): return np.log(np.clip(m0.predict(df),1e-6,None))

def cv_m1(feats=FEAT, structures=STRUCT, tag="M1"):
    tr,va,bs,it=[],[],[],[]
    for f in FOLDS:
        mtr,mva=fold_masks(dev,f); dtr,dva=dev[mtr],dev[mva]
        mm=M0Region().fit(dtr); otr,ova=off(mm,dtr),off(mm,dva)
        a=np.zeros(len(dtr)); b=np.zeros(len(dva))
        for _,pp in structures:
            ds=lgb.Dataset(dtr[feats],label=dtr["화재건수"].values,init_score=otr,categorical_feature=CATS,free_raw_data=False)
            dv=lgb.Dataset(dva[feats],label=dva["화재건수"].values,init_score=ova,categorical_feature=CATS,reference=ds,free_raw_data=False)
            m=lgb.train(dict(pp,seed=SEED),ds,num_boost_round=4000,valid_sets=[dv],
                        callbacks=[lgb.early_stopping(150,verbose=False)])
            it.append(m.best_iteration)
            a+=np.exp(m.predict(dtr[feats],raw_score=True)+otr)/len(structures)
            b+=np.exp(m.predict(dva[feats],raw_score=True)+ova)/len(structures)
        tr.append(pdev(dtr["화재건수"],a)); va.append(pdev(dva["화재건수"],b)); bs.append(pdev(dva["화재건수"],mm.predict(dva)))
    r=dict(cv_valid=float(np.mean(va)),cv_train=float(np.mean(tr)),cv_m0=float(np.mean(bs)),
           gap=float(np.mean(va)-np.mean(tr)),skill=float(np.mean(bs)-np.mean(va)),
           mean_best_iter=int(np.mean(it)))
    print(f"  [{tag:20s}] CV valid {r['cv_valid']:.4f}  skill {r['skill']:+.4f}  gap {r['gap']:+.4f}")
    return r

print("\n■ M1 — 최종 구성 CV")
cv1=cv_m1(); N1=int(cv1["mean_best_iter"]*1.15)
m0r=M0Region().fit(dev); od,oh=off(m0r,dev),off(m0r,ho)
mu1=np.zeros(len(ho)); models=[]
for nm,pp in STRUCT:
    for s in range(5):
        p=dict(pp,seed=SEED+s*101,bagging_seed=SEED+s*101,feature_fraction_seed=SEED+s*101)
        m=lgb.train(p,lgb.Dataset(dev[FEAT],label=dev["화재건수"].values,init_score=od,
                    categorical_feature=CATS,free_raw_data=False),num_boost_round=N1)
        m.save_model(str(OUTD/f"m1_{nm}_seed{s}.txt")); models.append(m)
        mu1+=np.exp(m.predict(ho[FEAT],raw_score=True)+oh)/10
REP["M1"]=occ_report("M1 v3 (2구조x5시드)",y_ho,mu1); REP["M1"].update(cv1,rounds=N1)

# 폴백 사다리 — M0(계절계수 포함) 은 단독 모델로 유지
m0full=M0().fit(dev); REP["M0"]=occ_report("M0 (폴백)",y_ho,m0full.predict(ho))
orc=ho.groupby("region_cd")["화재건수"].transform("mean").values
REP["oracle"]=occ_report("지역 oracle (상한)",y_ho,np.clip(orc,1e-6,None))
rng=np.random.RandomState(0)
REP["noise_floor"]=float(np.mean([pdev(rng.poisson(mu1),mu1) for _ in range(20)]))
print(f"  Poisson 잡음 바닥 {REP['noise_floor']:.4f}  →  M0 대비 여유 소진율 "
      f"{(REP['M0']['deviance']-REP['M1']['deviance'])/(REP['M0']['deviance']-REP['noise_floor'])*100:.1f}%")

# ============================================================ 누수 감사
print("\n■ 누수 감사")
A={}
BANNED=["발화요인","원인","최초착화물대분류","진화시간","진화시간_분","피해액_천원","사망","부상",
        "인명피해","연소확대","그을음면적","대형화재_진화120","대형화재_진화60","대형화재_인명",
        "대형화재_재산","공식대형","시각","장소중분류","화재유형","온도","습도","풍향","풍속구간"]
A["1_금지피처_부재"]=not bool(set(FEAT)&set(BANNED))
A["2_등급_전부등록"]=all(GRADE.get(f) in ("A","B") for f in FEAT)
print(f"  1) 사후확정(C등급) 피처 부재         {A['1_금지피처_부재']}")
print(f"  2) 전 피처 관측등급 등록            {A['2_등급_전부등록']}  (A {sum(GRADE[f]=='A' for f in FEAT)} / B {sum(GRADE[f]=='B' for f in FEAT)})")

# 3) 시간 파생이 과거만 보는가 — 수치 검증
s=dev.sort_values(["region_cd","날짜"])
ok_t1=bool((s.groupby("region_cd")["실효습도"].shift(1).round(1)
            .eq(s["실효습도_t1"].round(1)) | s["실효습도_t1"].isna()).all())
A["3_지연파생_과거만"]=ok_t1
print(f"  3) `_t1` 파생이 정확히 t-1        {ok_t1}")

# 4) 전년 이력이 당해를 보지 않는가
chk=dev.groupby(["region_cd","년"])["전년화재율"].nunique().max()
A["4_전년이력_연내상수"]=int(chk)<=1
print(f"  4) 전년 이력이 연내 상수(당해 미참조) {A['4_전년이력_연내상수']}")

# 5) 미래 정보 주입 대조 — 내일 기상을 넣으면 얼마나 좋아지나
fut=dev.sort_values(["region_cd","날짜"]).copy()
for c in ["기온","실효습도","풍속","강수량"]:
    fut[f"내일_{c}"]=fut.groupby("region_cd")[c].shift(-1)
FF=FEAT+[f"내일_{c}" for c in ["기온","실효습도","풍속","강수량"]]
tr_,va_=[],[]
for f in FOLDS:
    mtr,mva=fold_masks(fut,f); dtr,dva=fut[mtr],fut[mva]
    mm=M0Region().fit(dtr); otr,ova=off(mm,dtr),off(mm,dva)
    m=lgb.train(dict(P,seed=SEED),lgb.Dataset(dtr[FF],label=dtr["화재건수"].values,init_score=otr,
                categorical_feature=CATS,free_raw_data=False),num_boost_round=4000,
                valid_sets=[lgb.Dataset(dva[FF],label=dva["화재건수"].values,init_score=ova,
                categorical_feature=CATS,free_raw_data=False)],
                callbacks=[lgb.early_stopping(150,verbose=False)])
    va_.append(pdev(dva["화재건수"],np.exp(m.predict(dva[FF],raw_score=True)+ova)))
A["5_미래주입_이득"]=float(cv1["cv_valid"]-np.mean(va_))
print(f"  5) 내일 기상 주입 시 CV {np.mean(va_):.4f} (현재 {cv1['cv_valid']:.4f}, 이득 {A['5_미래주입_이득']:+.4f})")
print(f"     → 이득이 양수면 현재 피처가 미래를 '보지 않고 있다'는 뜻이다")

# 6) 라벨 셔플
sh=dev.copy(); sh["화재건수"]=rng.permutation(sh["화재건수"].values)
a_,b_,c_,d_=[pd.Timestamp(x) for x in FOLDS[-1]]
s1,s2=sh[sh["날짜"].between(a_,b_)],sh[sh["날짜"].between(c_,d_)]
mm=M0Region().fit(s1); o1,o2=off(mm,s1),off(mm,s2)
bs=lgb.train(dict(P,seed=SEED),lgb.Dataset(s1[FEAT],label=s1["화재건수"].values,init_score=o1,
             categorical_feature=CATS,free_raw_data=False),num_boost_round=N1)
A["6_셔플"]=[pdev(s2["화재건수"],np.exp(bs.predict(s2[FEAT],raw_score=True)+o2)),
             pdev(s2["화재건수"],np.full(len(s2),sh["화재건수"].mean()))]
print(f"  6) 라벨 셔플 {A['6_셔플'][0]:.4f} vs 상수 {A['6_셔플'][1]:.4f}")

# 7) 계절 정합 초과 gap
rows=[]
for f in FOLDS:
    mtr,mva=fold_masks(dev,f); dtr,dva=dev[mtr],dev[mva]
    mm=M0Region().fit(dtr); otr,ova=off(mm,dtr),off(mm,dva)
    m=lgb.train(dict(P,seed=SEED),lgb.Dataset(dtr[FEAT],label=dtr["화재건수"].values,init_score=otr,
                categorical_feature=CATS,free_raw_data=False),num_boost_round=4000,
                valid_sets=[lgb.Dataset(dva[FEAT],label=dva["화재건수"].values,init_score=ova,
                categorical_feature=CATS,free_raw_data=False)],
                callbacks=[lgb.early_stopping(150,verbose=False)])
    sub=dtr[dtr["월"].isin(set(dva["월"].unique()))]
    if not len(sub): rows.append({}); continue
    osub=off(mm,sub)
    rows.append({"m_tr":pdev(sub["화재건수"],np.exp(m.predict(sub[FEAT],raw_score=True)+osub)),
                 "m_va":pdev(dva["화재건수"],np.exp(m.predict(dva[FEAT],raw_score=True)+ova)),
                 "b_tr":pdev(sub["화재건수"],mm.predict(sub)),"b_va":pdev(dva["화재건수"],mm.predict(dva))})
sg=pd.DataFrame([r for r in rows if r])
gm=float((sg["m_va"]-sg["m_tr"]).mean()); gd=float((sg["b_va"]-sg["b_tr"]).mean())
A["7_초과gap"]=gm-gd
print(f"  7) 계절정합 gap {gm:+.4f} − 구조적 {gd:+.4f} = 초과 {gm-gd:+.4f}")

# 8) 미학습 지역
regs=np.array(sorted(dev["region_cd"].unique())); rng.shuffle(regs); sk=[]
for gset in np.array_split(regs,5):
    msk=dev["region_cd"].isin(set(gset)).values; dtr,dva=dev[~msk],dev[msk]
    mm=M0Region().fit(dtr); otr,ova=off(mm,dtr),off(mm,dva)
    m=lgb.train(dict(P,seed=SEED),lgb.Dataset(dtr[FEAT],label=dtr["화재건수"].values,init_score=otr,
                categorical_feature=CATS,free_raw_data=False),num_boost_round=N1)
    sk.append(pdev(dva["화재건수"],mm.predict(dva))-pdev(dva["화재건수"],np.exp(m.predict(dva[FEAT],raw_score=True)+ova)))
A["8_미학습지역_skill"]=float(np.mean(sk)); A["8_양수폴드"]=int(sum(1 for x in sk if x>0))
print(f"  8) 미학습 지역 skill {np.mean(sk):+.4f} ({A['8_양수폴드']}/5 양수)")
REP["audit"]=A

# ============================================================ M5
print("\n■ M5 — 발생확률")
oof=np.zeros(len(dev)); seen=np.zeros(len(dev),bool)
for f in FOLDS:
    mtr,mva=fold_masks(dev,f); dtr,dva=dev[mtr],dev[mva]
    mm=M0Region().fit(dtr); otr,ova=off(mm,dtr),off(mm,dva)
    acc=np.zeros(len(dva))
    for _,pp in STRUCT:
        m=lgb.train(dict(pp,seed=SEED),lgb.Dataset(dtr[FEAT],label=dtr["화재건수"].values,init_score=otr,
                    categorical_feature=CATS,free_raw_data=False),num_boost_round=N1)
        acc+=np.exp(m.predict(dva[FEAT],raw_score=True)+ova)/len(STRUCT)
    oof[mva]=acc; seen[mva]=True
iso=IsotonicRegression(out_of_bounds="clip",y_min=0,y_max=1).fit(
      1-np.exp(-oof[seen]),(dev["화재건수"].values[seen]>0).astype(float))
p_raw=1-np.exp(-mu1); p_cal=iso.predict(p_raw); occ_ho=(y_ho>0).astype(float)
REP["M5"]={"brier_raw":float(brier_score_loss(occ_ho,p_raw)),
           "brier_cal":float(brier_score_loss(occ_ho,p_cal)),
           "brier_const":float(brier_score_loss(occ_ho,np.full(len(occ_ho),occ_ho.mean())))}
print(f"  Brier 원값 {REP['M5']['brier_raw']:.5f} → 보정 {REP['M5']['brier_cal']:.5f} (상수 {REP['M5']['brier_const']:.5f})")
json.dump({"x":[float(v) for v in iso.X_thresholds_],"y":[float(v) for v in iso.y_thresholds_]},
          open(OUTD/"m5_isotonic.json","w"),ensure_ascii=False,indent=1)
json.dump({"global_mean":m0r.g,"m_smooth":m0r.m,"region_rate":{k:float(v) for k,v in m0r.rate.items()}},
          open(OUTD/"m1_offset_region_rate.json","w"),ensure_ascii=False,indent=1)
json.dump({"global_mean":m0full.g,"m_smooth":m0full.m,
           "region_rate":{k:float(v) for k,v in m0full.rate.items()},
           "season_coef":{k:float(v) for k,v in m0full.season.items()}},
          open(OUTD/"m0_lookup.json","w"),ensure_ascii=False,indent=1)
REP["features"]={"M1":FEAT,"cats":CATS}
REP["clusters"]={k:int(v) for k,v in clusters.items()}
json.dump(REP,open(OUTD/"report.json","w"),ensure_ascii=False,indent=1,default=float)
print(f"\n저장: {OUTD}  ({time.time()-t0:.0f}s)")
