import { q, q1, tx } from '../db/pool.js';
import { parseJson } from './riskService.js';

/** 산출 방식.
 *  'absolute' — 현재 인력 N(r) 을 알고 있어 인원 수로 낸다.
 *  'ratio'    — N 을 모를 때. 비율만 낸다. 지금은 ref_region_fire_staff 가
 *               250곳을 모두 덮으므로 실무에서는 absolute 로 떨어진다. */
export type Mode = 'ratio' | 'absolute' | 'unavailable';

/** 정책값 — 데이터가 아니라 사람이 정하는 값이다 (문서 §15).
 *
 *  중립값(무해한 값)은 자리마다 다르다. 곱해지는 계수는 1, 더해지는 항의 계수는 0,
 *  상한은 +∞, 하한은 0 이다. "모르면 0" 을 기계적으로 적용하면 상한이 0 이 되어
 *  모든 지역의 인력을 0명으로 만드는 답이 나온다.
 *
 *  **α 만은 중립값을 쓸 수 없다.** α=0 으로 풀면 목적함수는 79% 개선되지만
 *  그 답의 내용이 250곳 중 31곳을 0명으로 비우는 것이다(성남시분당구 396.7명 → 0명).
 *  부담이 작은 지역을 통째로 비워 큰 지역에 몰아주는 것이 수식상 최적이기 때문이다.
 *  그래서 α 는 자료가 없어도 사람이 정해야 하고, 화면에 드러내 운영자가 고르게 한다. */
export interface Policy {
  /** 최소 유지 비율 — 각 지역에 현재 인력의 최소 몇 배는 남긴다 (x ≥ α·N) */
  alpha: number;
  /** 배치 상한 배수 — 한 지역에 현재 인력의 몇 배까지 몰 수 있나 (x ≤ β·N) */
  beta: number;
  /** 일일 조정 상한 — 한 지역에서 하루에 최대 몇 명까지 바꾸나 (|x − N| ≤ δ) */
  delta: number;
}

export const DEFAULT_POLICY: Policy = { alpha: 0.7, beta: Infinity, delta: 50 };

const num = (v: unknown, dflt: number): number => {
  if (v == null || v === '') return dflt;
  const s = String(v).trim();
  if (s === '무제한' || s === 'inf' || s === 'Infinity') return Infinity;
  const n = Number(s);
  return Number.isFinite(n) ? n : dflt;
};

/** 화면·API 가 넘긴 값을 안전한 범위로 가둔다.
 *  α>1 이면 하한이 현재 인력보다 커져 총량 제약과 모순되고, β<1 이면 상한이
 *  현재 인력보다 작아 같은 문제가 생긴다. 둘 다 실행가능 영역을 비운다. */
export function readPolicy(qs: Record<string, unknown>): Policy {
  const alpha = Math.min(1, Math.max(0, num(qs.alpha, DEFAULT_POLICY.alpha)));
  const betaRaw = num(qs.beta, DEFAULT_POLICY.beta);
  const beta = betaRaw >= 1 ? betaRaw : 1;
  const delta = Math.max(0, num(qs.delta, DEFAULT_POLICY.delta));
  return { alpha, beta, delta };
}

export async function activeConstraints(): Promise<any[]> {
  return q(`SELECT constraint_id AS constraintId, region_cd AS regionCd,
                   constraint_type AS constraintType, value
              FROM ops_constraint WHERE is_active=1`);
}

interface Row {
  regionCd: string; sido: string; sigungu: string; runId: number;
  d: number; N: number; staffSource: string;
  riskGrade: string; rationale: any;
}

/** 시도 하나를 푼다 — 정렬 한 번과 반복문 하나.
 *
 *  풀려는 문제:
 *      min Σ d(r)·(need(r) − x(r))⁺   s.t. Σ x(r) = T,  lo(r) ≤ x(r) ≤ hi(r)
 *
 *  한 명을 지역 r 에 더 줄 때 목적함수가 줄어드는 폭(한계이득)은 x(r) < need(r)
 *  인 동안 d(r) 이고 need 를 넘으면 0 이다. 한계이득이 줄기만 하므로 "이득이 큰
 *  곳부터 채우기" 가 최적이다. 교환논법으로도 같다 — d(A) > d(B) 인데 A 가 아직
 *  need 에 못 미치는 채로 B 를 채웠다면 1명을 옮겨 d(A) − d(B) > 0 만큼 낮출 수 있다.
 *
 *  그래서 LP 풀이기가 필요 없다. scripts/alloc_optimize.py 의 `verify` 가
 *  scipy LP 와 9개 정책값 조합에서 대조하며, 오차는 1e-12 수준이다.
 */
function solveSido(idx: number[], d: number[], N: number[], need: number[], p: Policy): number[] {
  const T = idx.reduce((s, i) => s + N[i], 0);
  const lo: Record<number, number> = {};
  const hi: Record<number, number> = {};
  for (const i of idx) {
    const l = Math.max(p.alpha * N[i], N[i] - p.delta);
    const h = Math.min(Number.isFinite(p.beta) ? p.beta * N[i] : Infinity, N[i] + p.delta);
    lo[i] = Math.min(l, h);
    hi[i] = h;
  }
  const x: Record<number, number> = {};
  let left = T;
  for (const i of idx) { x[i] = lo[i]; left -= lo[i]; }

  // 1단계 — 한계이득 d 가 큰 곳부터 need 까지 (상한 hi 를 넘지 못한다)
  for (const i of [...idx].sort((a, b) => d[b] - d[a])) {
    if (left <= 1e-9) break;
    const room = Math.min(hi[i], need[i]) - x[i];
    if (room <= 0) continue;
    const g = Math.min(room, left);
    x[i] += g; left -= g;
  }
  // 2단계 — 더 줘도 목적함수가 안 줄어든다. 그러면 원래 자리로 되돌리는 데 쓴다.
  //          최적해가 여럿일 때 이동이 가장 적은 것을 고르는 것과 같다.
  if (left > 1e-9) {
    const room = idx.map((i) => Math.max(0, Math.min(hi[i], N[i]) - x[i]));
    const tot = room.reduce((s, v) => s + v, 0);
    if (tot > 0) {
      const g = Math.min(left, tot);
      idx.forEach((i, k) => { x[i] += room[k] * (g / tot); });
      left -= g;
    }
  }
  if (left > 1e-9) {                    // 그래도 남으면 상한까지 비례 배분
    const room = idx.map((i) => Math.max(0, hi[i] - x[i]));
    const tot = room.reduce((s, v) => s + v, 0);
    if (tot > 0) idx.forEach((i, k) => { x[i] += room[k] * (left / tot); });
  }
  return idx.map((i) => x[i]);
}

export async function buildProposal(date: string, policy: Policy = DEFAULT_POLICY, sido?: string) {
  const p: any[] = [date];
  let w = 'p.target_date=? AND p.is_current=1 AND p.data_status<>' + "'데이터없음'";
  if (sido) { w += ' AND r.sido=?'; p.push(sido); }

  // d(r) 은 expected_damage_load 만 쓴다. expected_count 로 폴백하지 않는다 —
  // 폴백하면 λ(1+q)(1+ℓ) 자리에 λ 가 들어와 번짐·장기화(부담의 약 29%)가 통째로
  // 빠진 값이 '대응 부담' 이라는 같은 이름으로 흘러간다. 없으면 없다고 해야 한다.
  const rows = await q<any>(
    `SELECT p.region_cd AS regionCd, r.sido, r.sigungu, p.run_id AS runId,
            p.expected_damage_load AS d, p.top_factors AS rationale, p.risk_grade AS riskGrade,
            s.staff_est AS N, s.staff_source AS staffSource
       FROM pred_daily p
       JOIN ref_region_profile r     ON r.region_cd=p.region_cd
       LEFT JOIN ref_region_fire_staff s ON s.region_cd=p.region_cd
      WHERE ${w} ORDER BY p.region_cd`, p);
  if (!rows.length) return null;

  const missingD = rows.filter((r: any) => r.d == null).length;
  const missingN = rows.filter((r: any) => r.N == null).length;

  // 부담 d 가 없으면 배분안을 내지 않는다.
  //
  // 결측을 0 으로 채우면 그 지역은 '위험이 없는 곳' 이 되어 목표 인원이 0 이 되고,
  // 전 지역이 결측인 날(2026-09-01~03 이 실제로 그랬다)에는 D=0 이라 모든 목표가
  // 0 이 되면서 "남는 위험 0 · 개선 없음" 이라는 그럴듯한 화면이 나온다.
  // 계산이 실패한 것과 위험이 없는 것은 전혀 다른데 화면에서는 구별되지 않는다.
  // M2a(확산)·M4(장기화)가 돌지 않은 날에는 언제든 다시 일어나므로,
  // 조용히 메우지 않고 산출을 거부한다.
  if (missingD > 0) {
    return {
      targetDate: date, mode: 'unavailable' as const,
      runId: rows[0].runId, missingDemand: missingD, missingStaff: missingN,
      total: rows.length,
      reason: '확산·장기화 예측이 없어 대응 부담을 계산할 수 없습니다',
      items: [], perSido: [],
    };
  }

  const mode: Mode = missingN === 0 ? 'absolute' : 'ratio';

  const d = rows.map((r: any) => Number(r.d));   // 결측은 위에서 이미 걸렀다
  const N = rows.map((r: any) => Number(r.N ?? 0));
  const D = d.reduce((s, v) => s + v, 0) || 1;
  const totalStaff = N.reduce((s, v) => s + v, 0);
  const need = d.map((v) => totalStaff * v / D);

  // 시도별로 나눠 푼다. 인력이 시도 경계를 넘지 못하므로 17개 독립 문제다.
  const x = N.slice();
  const bySido = new Map<string, number[]>();
  rows.forEach((r: any, i: number) => {
    const k = String(r.sido);
    if (!bySido.has(k)) bySido.set(k, []);
    bySido.get(k)!.push(i);
  });
  if (mode === 'absolute') {
    for (const idx of bySido.values()) {
      const xs = solveSido(idx, d, N, need, policy);
      idx.forEach((i, k) => { x[i] = xs[k]; });
    }
  }

  const obj = (xx: number[]) =>
    xx.reduce((s, v, i) => s + d[i] * Math.max(0, need[i] - v), 0);

  // 기준선 — 시도 총량은 지키되 시도 안에서는 위험을 보지 않고 똑같이 나눈 배치.
  // 이것이 '위험도를 쓰지 않았을 때' 의 성적이고, 모델의 값어치는 이것과의 차이다.
  const even = N.slice();
  for (const idx of bySido.values()) {
    const t = idx.reduce((s, i) => s + N[i], 0) / idx.length;
    for (const i of idx) even[i] = t;
  }

  const objStatusQuo = obj(N);
  const objOptimal = obj(x);
  const objEven = obj(even);
  const movedTotal = x.reduce((s, v, i) => s + Math.abs(v - N[i]), 0) / 2;
  const unmetTotal = x.reduce((s, v, i) => s + Math.max(0, need[i] - v), 0);

  const items = rows.map((r: any, i: number) => {
    const diff = x[i] - N[i];
    let gap: '부족' | '적정' | '잉여' = '적정';
    if (mode === 'absolute' && N[i] > 0) {
      const ratio = N[i] / (need[i] || 1);
      gap = ratio < 0.9 ? '부족' : ratio > 1.1 ? '잉여' : '적정';
    }
    return {
      regionCd: r.regionCd, sido: r.sido, sigungu: r.sigungu, riskGrade: r.riskGrade,
      demandScore: d[i], recommendedRatio: d[i] / D,
      needHeadcount: mode === 'absolute' ? need[i] : null,
      currentStaff: mode === 'absolute' ? N[i] : null,
      recommendedHeadcount: mode === 'absolute' ? x[i] : null,
      changeHeadcount: mode === 'absolute' ? diff : null,
      unmet: mode === 'absolute' ? Math.max(0, need[i] - x[i]) : null,
      gap, staffSource: r.staffSource ?? null,
      rationale: parseJson(r.rationale) ?? [],
    };
  }).sort((a, b) => b.demandScore - a.demandScore);

  const perSido = [...bySido.entries()].map(([s, idx]) => ({
    sido: s, n: idx.length,
    currentStaff: idx.reduce((t, i) => t + N[i], 0),
    needHeadcount: idx.reduce((t, i) => t + need[i], 0),
    unmet: idx.reduce((t, i) => t + Math.max(0, need[i] - x[i]), 0),
    moved: idx.reduce((t, i) => t + Math.abs(x[i] - N[i]), 0) / 2,
  })).sort((a, b) => b.unmet - a.unmet);

  return {
    targetDate: date, mode, runId: rows[0].runId,
    policy: { alpha: policy.alpha, beta: Number.isFinite(policy.beta) ? policy.beta : null, delta: policy.delta },
    totalDemand: D, totalStaff,
    staffSource: mode === 'absolute' ? '추정' : null,
    missingDemand: missingD, missingStaff: missingN,
    // 지표 세 개는 같은 자로 잰 값이다 — 단위는 '위험가중 미충족'.
    unmetRiskBefore: objStatusQuo,   // 지금 배치 그대로
    unmetRiskAfter: objOptimal,      // 권장안대로
    baselineUnmetRisk: objEven,      // 시도 안에서 똑같이 나눴을 때
    improvementVsNow: objStatusQuo > 0 ? 1 - objOptimal / objStatusQuo : null,
    improvementVsEven: objEven > 0 ? 1 - objOptimal / objEven : null,
    movedTotal, unmetTotal,
    sidoZero: perSido.filter((s) => s.unmet < 1e-6).length,
    perSido, items,
  };
}

/** 저장할 때만 DB 에 남긴다.
 *
 *  예전에는 조회할 때마다 proposal 을 INSERT 했다. 정책값을 화면에서 바꾸게 되면서
 *  슬라이더를 한 칸 움직일 때마다 250행이 쌓이게 되므로 조회는 계산만 하고,
 *  사람이 '고친 내용 저장' 을 누른 순간의 안만 이력으로 남긴다. 그래야 이력에
 *  남은 안과 실제로 조정된 안이 정확히 같아진다(FR-039). */
async function persistProposal(conn: any, prop: any): Promise<number> {
  const [res]: any = await conn.query(
    `INSERT INTO ops_allocation_proposal
      (target_date,run_id,mode,policy,unmet_risk_before,unmet_risk_after,
       baseline_unmet_risk,moved_total,staff_source)
     VALUES (?,?,?,?,?,?,?,?,?)`,
    [prop.targetDate, prop.runId, prop.mode, JSON.stringify(prop.policy),
     prop.unmetRiskBefore, prop.unmetRiskAfter, prop.baselineUnmetRisk,
     prop.movedTotal, prop.staffSource]);
  const id = res.insertId;
  const it = prop.items;
  await conn.query(
    `INSERT INTO ops_allocation_proposal_item
      (proposal_id,region_cd,demand_score,recommended_ratio,need_headcount,
       current_staff,recommended_headcount,rationale)
     VALUES ${it.map(() => '(?,?,?,?,?,?,?,?)').join(',')}`,
    it.flatMap((i: any) => [id, i.regionCd, i.demandScore, i.recommendedRatio,
      i.needHeadcount, i.currentStaff, i.recommendedHeadcount, JSON.stringify(i.rationale)]));
  return id;
}

/** FR-036 — 운영 제약 위반은 경고와 함께 거부한다 */
export async function checkViolations(items: any[]): Promise<any[]> {
  const cons = await activeConstraints();
  if (!cons.length) return [];
  const v: any[] = [];
  for (const it of items) {
    const head = it.adjustedHeadcount;
    if (head == null) continue;
    for (const c of cons) {
      if (c.regionCd && c.regionCd !== it.regionCd) continue;
      if (c.constraintType === '정원상한' && head > Number(c.value))
        v.push({ regionCd: it.regionCd, constraintType: c.constraintType, limit: Number(c.value), attempted: head });
      if (c.constraintType === '최소잔류' && head < Number(c.value))
        v.push({ regionCd: it.regionCd, constraintType: c.constraintType, limit: Number(c.value), attempted: head });
    }
  }
  return v;
}

export async function saveAdjustment(body: any) {
  const violations = await checkViolations(body.items ?? []);
  if (violations.length) return { ok: false as const, violations };

  // 저장 시점에 배분안을 다시 계산해 함께 남긴다. 화면이 들고 있던 proposalId 를
  // 믿지 않는 이유는, 그 안이 어떤 정책값으로 나온 것인지 서버가 알 수 없기 때문이다.
  const policy = readPolicy(body.policy ?? {});
  const prop = await buildProposal(body.targetDate, policy);
  if (!prop) return { ok: false as const, violations: [], error: '해당 날짜의 예측이 없습니다' };

  const id = await tx(async (c) => {
    const pid = await persistProposal(c, prop);
    const [res]: any = await c.query(
      `INSERT INTO ops_allocation_adjustment (proposal_id,author_id,author_role,reason)
       VALUES (?,?,?,?)`, [pid, body.authorId, body.authorRole, body.reason]);
    const aid = res.insertId;
    if (body.items?.length) {
      const recMap = new Map(prop.items.map((i: any) => [i.regionCd, i.recommendedHeadcount]));
      await c.query(
        `INSERT INTO ops_allocation_adjustment_item
          (adjustment_id,region_cd,recommended_headcount,adjusted_headcount,adjusted_ratio)
         VALUES ${body.items.map(() => '(?,?,?,?,?)').join(',')}`,
        body.items.flatMap((i: any) => [aid, i.regionCd,
          recMap.get(i.regionCd) ?? null, i.adjustedHeadcount ?? null, i.adjustedRatio ?? null]));
    }
    return aid;
  });
  return { ok: true as const, adjustmentId: id };
}

export async function listAdjustments(from?: string, to?: string) {
  const p: any[] = [];
  let w = '1=1';
  if (from) { w += ' AND p.target_date>=?'; p.push(from); }
  if (to) { w += ' AND p.target_date<=?'; p.push(to); }
  const heads = await q<any>(
    `SELECT a.adjustment_id AS adjustmentId, a.proposal_id AS proposalId, a.author_id AS authorId,
            a.author_role AS authorRole, a.reason, a.created_at AS createdAt,
            p.target_date AS targetDate, p.policy
       FROM ops_allocation_adjustment a
       JOIN ops_allocation_proposal p ON p.proposal_id=a.proposal_id
      WHERE ${w} ORDER BY a.adjustment_id DESC LIMIT 100`, p);
  if (!heads.length) return [];
  const ids = heads.map((h) => h.adjustmentId);
  const items = await q<any>(
    `SELECT adjustment_id AS adjustmentId, region_cd AS regionCd,
            recommended_headcount AS recommendedHeadcount,
            adjusted_headcount AS adjustedHeadcount, adjusted_ratio AS adjustedRatio
       FROM ops_allocation_adjustment_item
      WHERE adjustment_id IN (${ids.map(() => '?').join(',')})`, ids);
  const byId = new Map<number, any[]>();
  for (const i of items) {
    if (!byId.has(i.adjustmentId)) byId.set(i.adjustmentId, []);
    byId.get(i.adjustmentId)!.push(i);
  }
  return heads.map((h) => ({ ...h, policy: parseJson(h.policy), items: byId.get(h.adjustmentId) ?? [] }));
}
