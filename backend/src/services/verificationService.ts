import { q, q1 } from '../db/pool.js';

export async function history(from: string, to: string, regionCd?: string) {
  const p: any[] = [from, to];
  let w = 'target_date BETWEEN ? AND ?';
  if (regionCd) { w += ' AND region_cd=?'; p.push(regionCd); }
  return q(
    `SELECT target_date AS targetDate, region_cd AS regionCd, sido, sigungu,
            risk_grade AS predictedGrade, occur_probability AS occurProbability,
            expected_count AS expectedCount, actual_count AS actualCount, outcome
       FROM v_pred_verification WHERE ${w}
      ORDER BY target_date DESC, region_cd LIMIT 5000`, p);
}

/** FR-019 · SC-008 — 오경보·미탐지. SC-011 — 월별 캘리브레이션 ±15% */
/** 일상어 정확도 — 운영 화면에 통계 용어 대신 이것을 보여준다.
 *
 *  deviance 0.8869 같은 값은 '88.9% 틀렸다' 로 오독된다. 실제로는 백분율이 아니고
 *  0 이 만점도 아니다 (화재가 무작위라 완벽한 모델도 0.87 아래로 못 간다).
 *  같은 성능을 사람이 검산할 수 있는 형태로 다시 낸다.
 */
export async function plainAccuracy(from: string, to: string, sido?: string) {
  const p: any[] = [from, to];
  let w = 'target_date BETWEEN ? AND ?';
  if (sido) { w += ' AND sido=?'; p.push(sido); }

  // ① 기간 전체 건수 — 예측 합계 vs 실제 합계
  const tot = await q1<any>(
    `SELECT SUM(expected_count) pred, SUM(actual_count) act, COUNT(*) n
       FROM v_pred_verification WHERE ${w} AND expected_count IS NOT NULL`, p);
  const pred = Number(tot?.pred ?? 0);
  const act = Number(tot?.act ?? 0);
  const volumeAccuracy = act > 0 ? 1 - Math.abs(pred - act) / act : 0;

  // ② 확률 정확도 — 확률 10분위별 '말한 값 vs 실제 발생률' 의 최대 어긋남
  const deciles = await q<any>(
    `SELECT bucket, AVG(occur_probability) said, AVG(actual_count >= 1) happened, COUNT(*) n
       FROM (SELECT occur_probability, actual_count,
                    NTILE(10) OVER (ORDER BY occur_probability) bucket
               FROM v_pred_verification
              WHERE ${w} AND occur_probability IS NOT NULL) t
      GROUP BY bucket ORDER BY bucket`, p);
  const probBuckets = deciles.map((r: any) => ({
    said: Number(r.said), happened: Number(r.happened), n: Number(r.n),
    gapPp: (Number(r.said) - Number(r.happened)) * 100,
  }));
  const probMaxGapPp = probBuckets.length
    ? Math.max(...probBuckets.map((b: any) => Math.abs(b.gapPp))) : 0;

  // ③ 지역별 정확도 — 시군구별 기간 합계가 실제의 ±10 / ±20% 안에 드는 비율
  const regions = await q<any>(
    `SELECT region_cd, sigungu, SUM(expected_count) pred, SUM(actual_count) act
       FROM v_pred_verification WHERE ${w} AND expected_count IS NOT NULL
      GROUP BY region_cd, sigungu HAVING act > 0`, p);
  const errs = regions.map((r: any) =>
    Math.abs(Number(r.pred) - Number(r.act)) / Number(r.act));
  const within = (t: number) => errs.length
    ? errs.filter((e: number) => e <= t).length / errs.length : 0;
  const sorted = [...errs].sort((a, b) => a - b);
  const medianErr = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;

  // 검산용 예시 — 화재가 많은 5곳
  const samples = [...regions]
    .sort((a: any, b: any) => Number(b.act) - Number(a.act))
    .slice(0, 5)
    .map((r: any) => ({
      sigungu: r.sigungu, pred: Number(r.pred), act: Number(r.act),
      errPct: (Number(r.pred) - Number(r.act)) / Number(r.act) * 100,
    }));

  // 무발생 비율 — '하루 단위는 못 맞힌다' 는 설명의 근거. 화면에 하드코딩하면
  // 데이터가 바뀐 뒤에도 옛 숫자가 남아 거짓이 된다.
  const zero = await q1<any>(
    `SELECT AVG(actual_count = 0) r FROM v_pred_verification WHERE ${w}`, p);

  return {
    range: { from, to },
    days: Number(tot?.n ?? 0),
    zeroRate: Number(zero?.r ?? 0),
    volume: { predicted: pred, actual: act, accuracy: volumeAccuracy },
    probability: { maxGapPp: probMaxGapPp, buckets: probBuckets },
    region: { within10: within(0.10), within20: within(0.20), medianErrPct: medianErr * 100,
              n: regions.length, samples },
  };
}


export async function summary(from: string, to: string, sido?: string) {
  const p: any[] = [from, to];
  let w = 'target_date BETWEEN ? AND ?';
  if (sido) { w += ' AND sido=?'; p.push(sido); }

  const counts = await q<any>(
    `SELECT outcome, COUNT(*) c FROM v_pred_verification WHERE ${w} GROUP BY outcome`, p);
  const base = { hit: 0, false_alarm: 0, miss: 0, correct_reject: 0 };
  for (const r of counts) (base as any)[r.outcome] = Number(r.c);

  const months = await q<any>(
    `SELECT DATE_FORMAT(target_date,'%Y-%m') ym,
            SUM(expected_count) pred, SUM(actual_count) act
       FROM v_pred_verification WHERE ${w} GROUP BY ym ORDER BY ym`, p);
  const calibration: Record<string, number> = {};
  for (const m of months) calibration[m.ym] = Number(m.act) > 0 ? Number(m.pred) / Number(m.act) : 0;

  // SC-007 — '높음 이상' 지역의 실제 발생률이 전체 평균 대비 유의하게 높은가 (이항 근사)
  const rates = await q1<any>(
    `SELECT SUM(risk_grade IN ('높음','매우높음') AND actual_count>=1) hi_fire,
            SUM(risk_grade IN ('높음','매우높음'))                     hi_n,
            SUM(actual_count>=1)                                       all_fire,
            COUNT(*)                                                   all_n
       FROM v_pred_verification WHERE ${w}`, p);
  const pHi = Number(rates?.hi_n) ? Number(rates.hi_fire) / Number(rates.hi_n) : 0;
  const pAll = Number(rates?.all_n) ? Number(rates.all_fire) / Number(rates.all_n) : 0;
  const se = Number(rates?.hi_n) ? Math.sqrt(pAll * (1 - pAll) / Number(rates.hi_n)) : 0;
  const z = se > 0 ? (pHi - pAll) / se : 0;

  // 상위 20% 포착률
  const capture = await q1<any>(
    `SELECT SUM(actual_count) tot FROM v_pred_verification WHERE ${w}`, p);
  const top = await q<any>(
    `SELECT actual_count FROM v_pred_verification WHERE ${w}
      ORDER BY expected_count DESC LIMIT 18446744073709551615`, p);
  const nTop = Math.max(1, Math.round(top.length * 0.2));
  const captured = top.slice(0, nTop).reduce((s, r) => s + Number(r.actual_count), 0);

  // 등급별 실제 결과 — 이 화면의 주 지표다.
  //
  // 맞힘/헛경보 4칸은 확률 예측을 억지로 O/X 로 압축한 것이라, 확률이 정확해도
  // '헛경보가 절반' 처럼 읽힌다. 모델이 51% 라고 말했으면 절반은 안 나는 것이
  // 정상이다. 등급별로 '예측 확률 vs 실제 발생률' 을 나란히 두면 같은 데이터가
  // 정직하게 읽힌다.
  const byGrade = await q<any>(
    `SELECT risk_grade                       AS grade,
            COUNT(*)                         AS n,
            AVG(occur_probability)           AS predRate,
            AVG(actual_count >= 1)           AS actualRate,
            SUM(actual_count)                AS fires
       FROM v_pred_verification
      WHERE ${w} AND risk_grade IS NOT NULL
      GROUP BY risk_grade`, p);

  const ORDER = ['낮음', '보통', '높음', '매우높음'];
  const grades = byGrade
    .map((r: any) => ({
      grade: String(r.grade),
      n: Number(r.n),
      predRate: Number(r.predRate),
      actualRate: Number(r.actualRate),
      fires: Number(r.fires),
      gapPp: (Number(r.predRate) - Number(r.actualRate)) * 100,
    }))
    .sort((a: any, b: any) => ORDER.indexOf(a.grade) - ORDER.indexOf(b.grade));

  // --- US5 시나리오 3 — 확산·원인 검증 (T094) ---------------------------
  const spread = await spreadVerification(from, to, sido);
  const cause = await causeVerification(from, to, sido);

  // 경보 관점 지표 — 정밀도·재현율을 함께 낸다. 정밀도만 보면 '헛경보가 많다' 로,
  // 재현율만 보면 '많이 놓친다' 로 읽힌다. 둘은 한쪽을 올리면 다른 쪽이 내려간다.
  const alerted = base.hit + base.false_alarm;
  const occurred = base.hit + base.miss;
  const precision = alerted ? base.hit / alerted : 0;
  const recall = occurred ? base.hit / occurred : 0;

  return {
    ...base,
    spread,
    cause,
    grades,
    total: base.hit + base.false_alarm + base.miss + base.correct_reject,
    falseAlarm: base.false_alarm,
    correctReject: base.correct_reject,
    calibration,
    highGradeRate: pHi,
    overallRate: pAll,
    zScore: z,
    significant: Math.abs(z) >= 1.96,
    recallAt20: Number(capture?.tot) > 0 ? captured / Number(capture.tot) : 0,
    precision,
    recall,
    f1: precision + recall > 0 ? (2 * precision * recall) / (precision + recall) : 0,
    lift: pAll > 0 ? precision / pAll : 0,
    maxGapPp: grades.length ? Math.max(...grades.map((g: any) => Math.abs(g.gapPp))) : 0,
  };
}


/** 확산 예측 상위군의 실제 연소확대율 (US5 시나리오 3) */
export async function spreadVerification(from: string, to: string, sido?: string) {
  const p: any[] = [from, to];
  let w = 'p.target_date BETWEEN ? AND ? AND p.is_current=1 AND p.spread_probability IS NOT NULL';
  if (sido) { w += ' AND r.sido=?'; p.push(sido); }

  const rows = await q<any>(
    `SELECT p.spread_probability sp, a.fires, a.escalated
       FROM pred_daily p
       JOIN ref_region_profile r ON r.region_cd=p.region_cd
       JOIN ref_actual_spread a ON a.target_date=p.target_date AND a.region_cd=p.region_cd
      WHERE ${w}`, p);
  if (!rows.length) return null;

  const sorted = [...rows].sort((a, b) => Number(b.sp) - Number(a.sp));
  const nTop = Math.max(1, Math.round(sorted.length * 0.2));
  const sum = (xs: any[], k: string) => xs.reduce((s, r) => s + Number(r[k]), 0);
  const topRate = sum(sorted.slice(0, nTop), 'escalated') / Math.max(1, sum(sorted.slice(0, nTop), 'fires'));
  const allRate = sum(rows, 'escalated') / Math.max(1, sum(rows, 'fires'));
  return {
    topQuintileEscalationRate: topRate,
    overallEscalationRate: allRate,
    lift: allRate > 0 ? topRate / allRate : null,
    nRegionDays: rows.length,
  };
}

/** 원인 Top-3 적중률 — 실제 발화요인이 제시한 3개 안에 있었는가 (US5 시나리오 3) */
export async function causeVerification(from: string, to: string, sido?: string) {
  const p: any[] = [from, to];
  let w = 'c.target_date BETWEEN ? AND ?';
  if (sido) { w += ' AND r.sido=?'; p.push(sido); }

  const row = await q1<any>(
    `SELECT SUM(a.cnt) matched,
            (SELECT SUM(a2.cnt) FROM ref_actual_cause a2
               JOIN ref_region_profile r2 ON r2.region_cd=a2.region_cd
              WHERE a2.target_date BETWEEN ? AND ?${sido ? ' AND r2.sido=?' : ''}) total
       FROM pred_cause_top3 c
       JOIN ref_region_profile r ON r.region_cd=c.region_cd
       JOIN ref_actual_cause a ON a.target_date=c.target_date
                              AND a.region_cd=c.region_cd
                              AND a.cause_class=c.cause_class
      WHERE ${w}`,
    sido ? [from, to, sido, from, to, sido] : [from, to, from, to]);
  const matched = Number(row?.matched ?? 0);
  const total = Number(row?.total ?? 0);
  return {
    top3Accuracy: total > 0 ? matched / total : null,
    matched, total,
    constantBaseline: 0.917,   // 최빈 3클래스 고정 제시 (홀드아웃 실측)
  };
}
