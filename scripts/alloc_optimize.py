"""소방 인력 재배치 최적화 — 참조 구현.

docs/소방인력배치_최적화모델.pdf 의 정식화를 그대로 코드로 옮긴 것이다.
백엔드(TypeScript)와 문서의 숫자가 같은 곳에서 나오게 하려고 둔다.
둘 중 하나를 고치면 다른 하나가 어긋나므로, `verify` 로 대조한다.

    .venv/bin/python scripts/alloc_optimize.py solve   [--date YYYY-MM-DD]
    .venv/bin/python scripts/alloc_optimize.py verify  # 탐욕해 vs LP 대조
    .venv/bin/python scripts/alloc_optimize.py sens    # 정책값 민감도(몬테카를로)

정식화 (§09)
    상수   d(r) = λ(1+q)(1+ℓ),  D = Σd,  need(r) = (ΣN)·d(r)/D
    변수   x(r) 재배치 후 인력 · u(r) 미충족 · y(r) 이동량
    목적   min Σ d(r)·u(r) + c·Σ y(r)
    제약   (C1) Σ_{r∈R(s)} x = Σ_{r∈R(s)} N   시도별 총량
           (C2) u ≥ need − x      (C3) u ≥ 0
           (C4) y ≥ x − N         (C5) y ≥ N − x
           (C6) x ≥ α·N           (C7) x ≤ β·N
           (C8) |x − x⁰| ≤ δ
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "src"))
from fire_ml.data.db import shared  # noqa: E402

# 중립값 — §15. "모르면 0" 이 아니다. 자리마다 무해한 값이 다르다.
NEUTRAL = dict(alpha=0.0, beta=np.inf, c=0.0, delta=np.inf)
BIG = 1e9  # 풀이기에 넣을 ∞ 대용


@dataclass
class Panel:
    date: str
    region: np.ndarray   # region_cd
    sido: np.ndarray
    d: np.ndarray        # 개별 부담
    N: np.ndarray        # 현재 인력

    @property
    def D(self) -> float:
        return float(self.d.sum())

    @property
    def total_staff(self) -> float:
        return float(self.N.sum())

    @property
    def need(self) -> np.ndarray:
        return self.total_staff * self.d / self.D


def load(date: str | None = None) -> Panel:
    """그날의 부담 d 와 현재 인력 N.

    d 는 expected_damage_load 만 쓴다 — expected_count 로 폴백하지 않는다.
    폴백하면 λ(1+q)(1+ℓ) 자리에 λ 가 들어와 번짐·장기화가 통째로 빠진 값이
    같은 이름으로 흘러간다(§14). 없으면 없다고 해야 한다.
    """
    c = shared()
    with c.cursor() as cur:
        if date is None:
            cur.execute("SELECT MAX(target_date) FROM pred_daily WHERE is_current=1 AND target_date<=CURDATE()")
            date = str(cur.fetchone()[0])
        cur.execute(
            """SELECT p.region_cd, r.sido, p.expected_damage_load, s.staff_est
                 FROM pred_daily p
                 JOIN ref_region_profile r   ON r.region_cd = p.region_cd
                 JOIN ref_region_fire_staff s ON s.region_cd = p.region_cd
                WHERE p.target_date=%s AND p.is_current=1
                ORDER BY p.region_cd""", (date,))
        rows = cur.fetchall()
    miss = [r[0] for r in rows if r[2] is None]
    if miss:
        raise SystemExit(f"{date}: expected_damage_load 결측 {len(miss)}곳 — 배분안을 낼 수 없다 (예: {miss[:3]})")
    return Panel(
        date=str(date),
        region=np.array([r[0] for r in rows]),
        sido=np.array([r[1] for r in rows]),
        d=np.array([float(r[2]) for r in rows]),
        N=np.array([float(r[3]) for r in rows]),
    )


# --------------------------------------------------------------- 목적함수 ---
def objective(p: Panel, x: np.ndarray, c: float = 0.0) -> float:
    u = np.maximum(0.0, p.need - x)
    y = np.abs(x - p.N)
    return float(p.d @ u + c * y.sum())


# ------------------------------------------------------- 탐욕해 (중립값) ---
def solve_greedy(p: Panel, alpha: float = 0.0, beta: float = np.inf,
                 delta: float = np.inf, x0: np.ndarray | None = None) -> np.ndarray:
    """정렬 + 반복문만으로 구한 최적해. LP 풀이기와 같은 답을 낸다.

    **왜 탐욕법으로 충분한가.** c=0 일 때 이 문제는 시도마다 이렇게 생겼다 —

        min Σ d(r)·(need(r) − x(r))⁺   s.t.  Σ x(r) = T,  lo(r) ≤ x(r) ≤ hi(r)

    한 명을 지역 r 에 더 주면 목적함수가 얼마나 줄어드는가(한계이득)를 보면
    x(r) < need(r) 인 동안은 d(r) 이고, need 를 넘으면 0 이다. 즉 한계이득이
    **줄기만 하고 늘지 않는다.** 이런 문제는 "지금 이득이 가장 큰 곳부터
    채우기" 가 최적이다.

    교환논법으로 다시 확인하면 — d(A) > d(B) 인데 A 가 아직 need 에 못 미치는
    상태에서 B 를 채운 배치가 있다면, 1명을 B 에서 A 로 옮겨 목적함수를
    d(A) − d(B) > 0 만큼 낮출 수 있다. 그런 배치는 최적일 수 없다.

    그래서 백엔드에 LP 풀이기를 넣을 필요가 없다. 정렬 한 번과 반복문 하나면
    같은 답이 나온다. `verify` 가 scipy LP 와 소수점 아래까지 대조한다.

    **놓치기 쉬운 것 — 인력을 내놓는 쪽이 '여유 있는 지역'만이 아니다.**
    α=0 이면 need 에 한참 못 미치는 지역이라도 d 가 작으면 0명까지 비워서
    d 가 큰 곳에 넘기는 것이 목적함수상 이득이다. 여유분만 옮기는 배치로
    풀면 목적함수가 2.3배 나빠진다. 이 성질이 α 를 반드시 정해야 하는 이유다.

    최적해가 여럿일 때는 **이동이 가장 적은 것**을 고른다(2단계). c 를 0 이
    아니라 0⁺ 로 둔 것과 같다. 값이 같은 답 중에 굳이 더 옮길 이유가 없다.
    """
    if x0 is None:
        x0 = p.N
    need = p.need
    x = np.empty_like(p.N)

    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        N_, d_, need_, x0_ = p.N[i], p.d[i], need[i], x0[i]
        T = float(N_.sum())

        lo = np.maximum(alpha * N_, x0_ - delta) if np.isfinite(delta) else alpha * N_
        hi = np.minimum(beta * N_ if np.isfinite(beta) else np.full(len(i), np.inf),
                        x0_ + delta if np.isfinite(delta) else np.full(len(i), np.inf))
        lo = np.minimum(lo, hi)

        xi = lo.copy()
        left = T - xi.sum()          # 아직 배치하지 않은 인원. x=N 이 허용되므로 ≥ 0

        # 1단계 — 한계이득 d 가 큰 곳부터 need 까지 (상한 hi 를 넘지 못한다)
        for k in np.argsort(-d_):
            if left <= 1e-12:
                break
            room = min(hi[k], need_[k]) - xi[k]
            if room <= 0:
                continue
            g = min(room, left)
            xi[k] += g
            left -= g

        # 2단계 — 남은 인원은 더 줘도 목적함수가 안 줄어든다(한계이득 0).
        # 그러면 **원래 자리로 되돌리는 데** 쓴다. 이동을 최소화하는 선택이다.
        if left > 1e-12:
            room = np.minimum(hi, N_) - xi          # N 까지 되돌릴 여지
            room = np.maximum(room, 0.0)
            if room.sum() > 0:
                g = np.minimum(room, room * (left / room.sum()))
                xi += g
                left -= g.sum()
        if left > 1e-12:                            # 그래도 남으면 상한까지 비례 배분
            room = np.maximum(hi - xi, 0.0)
            if room.sum() > 0:
                xi += room * (left / room.sum())

        x[i] = xi
    return x


# ------------------------------------------------------------- 일반 LP ---
def solve_lp(p: Panel, alpha=0.0, beta=np.inf, c=0.0, delta=np.inf,
             x0: np.ndarray | None = None) -> np.ndarray:
    """임의의 정책값에서의 최적해. 시도별로 나눠 푼다 (§09 — 17개 독립 문제)."""
    from scipy.optimize import linprog

    if x0 is None:
        x0 = p.N
    need = p.need
    x = np.empty_like(p.N)

    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        n = len(i)
        d_, N_, need_, x0_ = p.d[i], p.N[i], need[i], x0[i]
        T = float(N_.sum())

        # 변수 순서 [x(n) | u(n) | y(n)]
        obj = np.concatenate([np.zeros(n), d_, np.full(n, c)])

        A_eq = np.zeros((1, 3 * n)); A_eq[0, :n] = 1.0
        b_eq = np.array([T])

        A_ub = np.zeros((3 * n, 3 * n)); b_ub = np.zeros(3 * n)
        for k in range(n):
            A_ub[k, k] = -1.0; A_ub[k, n + k] = -1.0; b_ub[k] = -need_[k]        # u ≥ need − x
            A_ub[n + k, k] = 1.0; A_ub[n + k, 2 * n + k] = -1.0; b_ub[n + k] = N_[k]   # y ≥ x − N
            A_ub[2 * n + k, k] = -1.0; A_ub[2 * n + k, 2 * n + k] = -1.0; b_ub[2 * n + k] = -N_[k]

        lo = np.maximum(alpha * N_, x0_ - delta)
        hi = np.minimum(beta * N_ if np.isfinite(beta) else np.full(n, BIG),
                        x0_ + delta if np.isfinite(delta) else np.full(n, BIG))
        lo = np.minimum(lo, hi)  # 수치오차로 lo>hi 가 되는 것 방지
        bounds = ([(float(a), float(b)) for a, b in zip(lo, hi)]
                  + [(0.0, None)] * n + [(0.0, None)] * n)

        r = linprog(obj, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                    bounds=bounds, method="highs")
        if not r.success:
            raise RuntimeError(f"{s}: LP 실패 — {r.message}")
        x[i] = r.x[:n]
    return x


# ---------------------------------------------------------------- 리포트 ---
def report(p: Panel, x: np.ndarray, c: float = 0.0) -> dict:
    need = p.need
    u = np.maximum(0.0, need - x)
    y = np.abs(x - p.N)
    status = objective(p, p.N, c)

    # 시도 안에서 똑같이 나눈 경우 — C1 을 지키는 '위험을 안 본' 기준선
    even = np.empty_like(x)
    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        even[i] = p.N[i].sum() / len(i)

    per_sido = []
    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        per_sido.append(dict(
            sido=str(s), n=int(len(i)),
            N=float(p.N[i].sum()), need=float(need[i].sum()),
            unmet=float(u[i].sum()), obj=float(p.d[i] @ u[i]),
            moved=float(y[i].sum() / 2)))
    per_sido.sort(key=lambda r: -r["unmet"])

    return dict(
        date=p.date, n=int(len(p.d)),
        D=p.D, total_staff=p.total_staff,
        obj_status_quo=status,
        obj_even=objective(p, even, c),
        obj_optimal=objective(p, x, c),
        improvement=1 - objective(p, x, c) / status if status else None,
        unmet_total=float(u.sum()),
        moved_total=float(y.sum() / 2),
        sido_zero=int(sum(1 for r in per_sido if r["unmet"] < 1e-6)),
        per_sido=per_sido,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["solve", "verify", "sens"])
    ap.add_argument("--date")
    ap.add_argument("--draws", type=int, default=400)
    ap.add_argument("--out")
    a = ap.parse_args()

    p = load(a.date)
    if a.cmd == "solve":
        x = solve_greedy(p)
        r = report(p, x)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        if a.out:
            Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    elif a.cmd == "verify":
        cases = [(0.0, np.inf, np.inf), (0.3, np.inf, np.inf), (0.7, np.inf, np.inf),
                 (0.9, np.inf, np.inf), (0.7, 1.5, np.inf), (0.7, np.inf, 20.0),
                 (0.7, 1.5, 50.0), (0.5, 2.0, 100.0), (0.0, np.inf, 10.0)]
        print(f"{'α':>5} {'β':>6} {'δ':>7} {'탐욕해':>12} {'LP':>12} {'차이':>10}  판정")
        bad = 0
        for al, be, de in cases:
            xg = solve_greedy(p, alpha=al, beta=be, delta=de)
            xl = solve_lp(p, alpha=al, beta=be, delta=de)
            og, ol = objective(p, xg), objective(p, xl)
            ok = abs(og - ol) <= 1e-6 * max(1.0, abs(ol))
            bad += not ok
            print(f"{al:>5.1f} {be:>6.1f} {de:>7.1f} {og:>12,.4f} {ol:>12,.4f} "
                  f"{abs(og-ol):>10.2e}  {'일치' if ok else '불일치'}")
        print(f"\n{len(cases)}개 조합 중 불일치 {bad}건")
        raise SystemExit(1 if bad else 0)
    elif a.cmd == "sens":
        run_sens(p, a.draws, a.out)


def run_sens(p: Panel, draws: int, out: str | None) -> None:
    """정책값 민감도 — 없는 값을 '아는 척' 하는 게 아니라 '몰라도 되는지' 를 잰다.

    정규분포는 쓰지 않는다. α∈[0,1], β≥1, c≥0, δ≥0 로 모두 한쪽이 막혀 있어
    정규분포는 의미 없는 값(음수 상한 등)에 확률을 준다. 경계가 있는 분포를 쓴다.
    """
    rng = np.random.default_rng(20260904)
    base = objective(p, solve_greedy(p))
    rows = []
    for _ in range(draws):
        alpha = rng.uniform(0.0, 0.9)                 # 최소 유지 비율
        beta = 1.0 + rng.gamma(2.0, 0.35)             # 배치 상한 배수 (≥1)
        c = rng.gamma(1.5, 0.20)                      # 이동 단가 (≥0)
        delta = rng.gamma(2.0, 60.0)                  # 일일 변화 상한 (명)
        x = solve_lp(p, alpha=alpha, beta=beta, c=c, delta=delta)
        rows.append(dict(alpha=alpha, beta=beta, c=c, delta=delta,
                         unmet_obj=float(p.d @ np.maximum(0.0, p.need - x)),
                         moved=float(np.abs(x - p.N).sum() / 2)))
    arr = np.array([r["unmet_obj"] for r in rows])
    mv = np.array([r["moved"] for r in rows])
    res = dict(
        date=p.date, draws=draws, base_obj=base,
        obj=dict(min=float(arr.min()), p05=float(np.percentile(arr, 5)),
                 median=float(np.median(arr)), p95=float(np.percentile(arr, 95)),
                 max=float(arr.max()), mean=float(arr.mean())),
        moved=dict(median=float(np.median(mv)), p95=float(np.percentile(mv, 95)),
                   max=float(mv.max())),
        worse_than_base=float((arr > base + 1e-9).mean()),
        within_10pct=float((arr <= base * 1.10 + 1e-9).mean()),
        samples=rows[:20],
    )
    print(json.dumps({k: v for k, v in res.items() if k != "samples"}, ensure_ascii=False, indent=1))
    if out:
        Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()


# =========================================================== 기법 비교 ===
# 같은 자료·같은 제약에 목적함수만 바꿔 푼다. "LP 가 타당했다" 를 말로 하지 않고
# 숫자로 보이기 위한 것이다. 비교가 공정하려면 제약이 같아야 하므로
# 시도별 총량 · α · δ 는 모두 동일하게 건다.

def solve_qp(p: Panel, alpha=0.0, beta=np.inf, delta=np.inf) -> np.ndarray:
    """이차계획 — 부족을 제곱해 벌한다. min Σ d(r)·u(r)²

    LP 와 뭐가 다른가: LP 는 부족 1명의 아픔이 늘 같다고 본다(선형).
    QP 는 부족이 커질수록 1명이 더 아프다고 본다(제곱). 그래서 QP 는
    한 곳에 부족이 몰리는 것을 싫어하고 여러 곳에 얇게 펴려 한다.
    """
    from scipy.optimize import minimize

    need = p.need
    x = np.empty_like(p.N)
    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        d_, N_, need_ = p.d[i], p.N[i], need[i]
        T = float(N_.sum())
        lo = np.maximum(alpha * N_, N_ - delta) if np.isfinite(delta) else alpha * N_
        hi = np.minimum(beta * N_ if np.isfinite(beta) else np.full(len(i), np.inf),
                        N_ + delta if np.isfinite(delta) else np.full(len(i), np.inf))
        lo = np.minimum(lo, hi)

        def f(v):
            u = np.maximum(0.0, need_ - v)
            return float(d_ @ (u ** 2))

        def g(v):                       # 기울기 — 없으면 수치미분이라 느리고 부정확하다
            u = np.maximum(0.0, need_ - v)
            return -2.0 * d_ * u

        r = minimize(f, N_.copy(), jac=g, method="SLSQP",
                     bounds=list(zip(lo, hi)),
                     constraints=[{"type": "eq", "fun": lambda v: v.sum() - T,
                                   "jac": lambda v: np.ones(len(v))}],
                     options={"maxiter": 800, "ftol": 1e-10})
        x[i] = r.x
    return x


def solve_minimax(p: Panel, alpha=0.0, beta=np.inf, delta=np.inf) -> np.ndarray:
    """최소최대 — 가장 나쁜 지역 하나를 최대한 낫게. min max_r d(r)·u(r)

    보조변수 t 를 두면 선형으로 쓸 수 있다: min t  s.t.  d(r)·u(r) ≤ t.
    전체 합이 아니라 최악값만 보므로, 한 곳을 구하려고 나머지를 희생한다.
    """
    from scipy.optimize import linprog

    need = p.need
    x = np.empty_like(p.N)
    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        n = len(i)
        d_, N_, need_ = p.d[i], p.N[i], need[i]
        T = float(N_.sum())

        # 변수 [x(n) | u(n) | t]
        obj = np.concatenate([np.zeros(2 * n), [1.0]])
        A_eq = np.zeros((1, 2 * n + 1)); A_eq[0, :n] = 1.0
        A_ub = np.zeros((2 * n, 2 * n + 1)); b_ub = np.zeros(2 * n)
        for k in range(n):
            A_ub[k, k] = -1.0; A_ub[k, n + k] = -1.0; b_ub[k] = -need_[k]   # u ≥ need − x
            A_ub[n + k, n + k] = d_[k]; A_ub[n + k, 2 * n] = -1.0           # d·u ≤ t
        lo = np.maximum(alpha * N_, N_ - delta) if np.isfinite(delta) else alpha * N_
        hi = np.minimum(beta * N_ if np.isfinite(beta) else np.full(n, np.inf),
                        N_ + delta if np.isfinite(delta) else np.full(n, np.inf))
        lo = np.minimum(lo, hi)
        bounds = ([(float(a), float(b)) for a, b in zip(lo, hi)]
                  + [(0.0, None)] * n + [(0.0, None)])
        r = linprog(obj, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=np.array([T]),
                    bounds=bounds, method="highs")
        if not r.success:
            raise RuntimeError(f"{s}: minimax 실패 — {r.message}")
        x[i] = r.x[:n]
    return x


def solve_even(p: Panel) -> np.ndarray:
    """시도 안에서 위험을 보지 않고 똑같이 나눈 배치. '모델을 안 썼을 때' 의 성적."""
    x = np.empty_like(p.N)
    for s in np.unique(p.sido):
        i = np.where(p.sido == s)[0]
        x[i] = p.N[i].sum() / len(i)
    return x


def compare(p: Panel, alpha=0.7, delta=50.0) -> list[dict]:
    """같은 제약에서 목적함수만 바꿔 풀고, 여러 잣대로 함께 잰다.

    잣대를 하나만 쓰면 자기 목적함수를 쓴 기법이 이기는 것이 당연해진다.
    그래서 '총 부족', '가장 나쁜 지역', '이동 인원' 을 같이 본다.
    """
    need = p.need
    cases = [
        ("현상 유지", p.N.copy(), "아무도 옮기지 않는다"),
        ("시도 안 균등", solve_even(p), "위험을 보지 않고 똑같이 나눈다"),
        ("목표 그대로", need.copy(), "시도 경계를 무시한다 — 실행 불가"),
        ("LP (채택)", solve_greedy(p, alpha=alpha, delta=delta), "부족 합을 최소화"),
        ("QP (제곱 벌점)", solve_qp(p, alpha=alpha, delta=delta), "부족을 제곱해 벌한다"),
        ("Minimax", solve_minimax(p, alpha=alpha, delta=delta), "가장 나쁜 지역을 최소화"),
    ]
    out = []
    for name, x, note in cases:
        u = np.maximum(0.0, need - x)
        # 시도 총량을 얼마나 어겼는가. 판정 기준은 0.001명 —
        # QP 풀이기(SLSQP)는 등식을 수치적으로 맞추므로 1e-5 명 수준의 오차가 남는다.
        # 그것을 '제약 위반' 이라고 부르면 기법 비교가 왜곡된다. 반면 '목표 그대로'는
        # 수천 명 단위로 어긋나므로 같은 기준에서 확실히 걸러진다.
        viol = max(abs(x[np.where(p.sido == s)[0]].sum()
                       - p.N[np.where(p.sido == s)[0]].sum())
                   for s in np.unique(p.sido))
        out.append(dict(
            name=name, note=note, feasible=bool(viol < 1e-3), violation=float(viol),
            obj=float(p.d @ u),               # LP 의 잣대
            obj_sq=float(p.d @ (u ** 2)),     # QP 의 잣대
            worst=float((p.d * u).max()),     # Minimax 의 잣대
            unmet=float(u.sum()),
            worst_region=str(p.region[int((p.d * u).argmax())]),
            moved=float(np.abs(x - p.N).sum() / 2),
            zero=int((x < 0.5).sum()),
        ))
    return out
