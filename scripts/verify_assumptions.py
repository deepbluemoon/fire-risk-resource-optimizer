"""확장편(21절~)의 수치를 다시 계산해 찍어 보는 **검산 스크립트**.

    .venv/bin/python scripts/verify_assumptions.py

문서 본문에는 이 값들이 그대로 적혀 있다. 데이터가 바뀌었을 때
문서와 어긋나지 않는지 확인하려면 이것을 돌려 대조한다.
"""
from __future__ import annotations
import glob, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml" / "src"))


# ------------------------------------------------- 포아송 가정 검사 ---
def poisson_checks() -> dict:
    from scipy.stats import poisson
    fs = sorted(glob.glob(str(ROOT / "data/cache/occurrence_*.parquet")))
    d = pd.concat([pd.read_parquet(f, columns=["날짜", "시도", "시군구", "화재건수",
                                               "월", "실효습도"]) for f in fs],
                  ignore_index=True)
    d["키"] = d["시도"] + "|" + d["시군구"]
    y = d["화재건수"].astype(float)
    m, v = float(y.mean()), float(y.var(ddof=1))

    freq = []
    for k in range(6):
        freq.append(dict(k=k, obs=float((y == k).mean()), exp=float(poisson.pmf(k, m))))
    freq.append(dict(k=6, obs=float((y >= 6).mean()), exp=float(poisson.sf(5, m))))

    # 지역 안에서만 본 과산포 — 전체 과산포가 '지역 간 차이' 때문인지 가른다
    g = d.groupby("키")["화재건수"].agg(["mean", "var", "count"])
    g = g[g["count"] > 100]
    ratio = (g["var"] / g["mean"]).replace([np.inf, -np.inf], np.nan).dropna()

    # 조건을 더할수록 자기상관이 줄어드는가
    d = d.sort_values(["키", "날짜"])
    def ac(res):
        t = pd.DataFrame({"r": res.values, "k": d["키"].values})
        t["p"] = t.groupby("k")["r"].shift(1)
        t = t.dropna()
        return float(np.corrcoef(t["r"], t["p"])[0, 1])
    yy = d["화재건수"].astype(float)
    steps = [("아무 조건 없음", ac(yy))]
    steps.append(("지역 평균 제거", ac(yy - d.groupby("키")["화재건수"].transform("mean"))))
    steps.append(("지역 × 월 제거", ac(yy - d.groupby(["키", "월"])["화재건수"].transform("mean"))))
    d["건조"] = pd.qcut(d["실효습도"].fillna(d["실효습도"].median()), 10,
                        labels=False, duplicates="drop")
    steps.append(("+ 건조도 10구간", ac(yy - d.groupby(["키", "월", "건조"])["화재건수"].transform("mean"))))

    return dict(
        n=int(len(y)), mean=m, var=v, ratio=v / m,
        obs0=float((y == 0).mean()), exp0=float(np.exp(-m)),
        freq=freq,
        within=dict(n=int(len(ratio)), median=float(ratio.median()),
                    mean=float(ratio.mean()), p90=float(ratio.quantile(0.9)),
                    over15=int((ratio > 1.5).sum())),
        autocorr=[dict(step=a, r=b) for a, b in steps],
    )


# --------------------------------------------- 역인과 함정 (진화시간) ---
def endogeneity() -> dict:
    fs = sorted(glob.glob(str(ROOT / "data/cache/spread_*.parquet")))
    d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    d = d[d["진화시간_이상치"] == 0]
    t = d["진화시간_분"].astype(float)
    d["거리구간"] = pd.qcut(d["소방서거리"].astype(float), 5, labels=False, duplicates="drop")
    g = d.groupby("거리구간", observed=True).agg(
        med=("진화시간_분", "median"), mean=("진화시간_분", "mean"),
        n=("진화시간_분", "size"), spread=("연소확대", "mean"))
    bands = [dict(band=int(i), med=float(r["med"]), mean=float(r["mean"]),
                  n=int(r["n"]), spread=float(r["spread"])) for i, r in g.iterrows()]
    by_sp = {}
    for v, lab in [(0, "안 번짐"), (1, "번짐")]:
        m = d["연소확대"] == v
        by_sp[lab] = dict(n=int(m.sum()), med=float(t[m].median()), mean=float(t[m].mean()))
        gg = d[m].groupby("거리구간", observed=True)["진화시간_분"].median()
        by_sp[lab]["by_band"] = [float(x) for x in gg.values]
    return dict(
        n=int(len(d)), med=float(t.median()), mean=float(t.mean()),
        corr=float(np.corrcoef(d["소방서거리"].astype(float), t)[0, 1]),
        bands=bands, by_spread=by_sp,
        zero_share=float((t == 0).mean()),
    )


# ------------------------------- 다자원 정식화가 LP 로 남는지 검증 ---
def multiresource() -> dict:
    """작은 가상 예제. 실제 소방 자료가 아니라 **식이 성립하는지**만 본다.

    유효 출동력 v = min(x/a, z/b) 를 부등식 둘로 바꾼 LP 가
    전수 탐색과 같은 답을 내는지 확인한다.
    """
    from scipy.optimize import linprog
    n = 3
    d = np.array([2.0, 1.0, 0.5])
    need = np.array([6.0, 3.0, 2.0])
    Nx, Nz, a, b = 30.0, 6.0, 4.0, 1.0
    N = 4 * n
    c = np.concatenate([np.zeros(3 * n), d])
    A_eq = np.zeros((2, N)); A_eq[0, :n] = 1; A_eq[1, n:2 * n] = 1
    rows, rhs = [], []
    for k in range(n):
        r = np.zeros(N); r[2 * n + k] = 1; r[k] = -1 / a; rows.append(r); rhs.append(0.0)
        r = np.zeros(N); r[2 * n + k] = 1; r[n + k] = -1 / b; rows.append(r); rhs.append(0.0)
        r = np.zeros(N); r[3 * n + k] = -1; r[2 * n + k] = -1; rows.append(r); rhs.append(-need[k])
    res = linprog(c, A_ub=np.array(rows), b_ub=np.array(rhs), A_eq=A_eq,
                  b_eq=np.array([Nx, Nz]), bounds=[(0, None)] * N, method="highs")
    x, z, v = res.x[:n], res.x[n:2 * n], res.x[2 * n:3 * n]
    lp_obj = float(d @ np.maximum(0, need - v))

    best = 1e9
    for x1 in np.arange(0, Nx + 1e-9, 0.5):
        for x2 in np.arange(0, Nx - x1 + 1e-9, 0.5):
            xv = np.array([x1, x2, Nx - x1 - x2])
            for z1 in np.arange(0, Nz + 1e-9, 0.25):
                for z2 in np.arange(0, Nz - z1 + 1e-9, 0.25):
                    zv = np.array([z1, z2, Nz - z1 - z2])
                    vv = np.minimum(xv / a, zv / b)
                    best = min(best, float(d @ np.maximum(0, need - vv)))
    return dict(d=[float(t) for t in d], need=[float(t) for t in need],
                Nx=Nx, Nz=Nz, a=a, b=b,
                x=[float(t) for t in x], z=[float(t) for t in z], v=[float(t) for t in v],
                lp_obj=lp_obj, brute=best, diff=abs(lp_obj - best))


def main() -> None:
    P = poisson_checks(); E = endogeneity(); M = multiresource()
    print("── 포아송 가정 (24절) ──")
    print(f"  표본 {P['n']:,} 지역·일")
    print(f"  평균 {P['mean']:.4f} · 분산 {P['var']:.4f} · 과산포비 {P['ratio']:.4f}")
    print(f"  0건 실제 {P['obs0']:.4f} vs 포아송 {P['exp0']:.4f} ({P['obs0']-P['exp0']:+.4f})")
    w = P["within"]
    print(f"  지역 내 과산포비 {w['n']}곳 · 중앙 {w['median']:.4f} · 90% {w['p90']:.4f} · 1.5 초과 {w['over15']}곳")
    print("  자기상관 " + " → ".join("%+.4f" % s["r"] for s in P["autocorr"]))
    print("\n── 역인과 (27절) ──")
    print(f"  표본 {E['n']:,}건 · 중앙 {E['med']:.1f}분 · 평균 {E['mean']:.1f}분 · 상관 {E['corr']:+.4f}")
    for b in E["bands"]:
        print(f"    {b['band']+1}분위 중앙 {b['med']:.1f}분 · 번짐률 {b['spread']:.1%} · {b['n']:,}건")
    for k, v in E["by_spread"].items():
        print(f"    {k} {v['n']:,}건 중앙 {v['med']:.1f}분 평균 {v['mean']:.1f}분")
    print("\n── 다자원 LP (22절) ──")
    print(f"  LP {M['lp_obj']:.6f} vs 전수탐색 {M['brute']:.6f} · 차이 {M['diff']:.1e}")


if __name__ == "__main__":
    main()
