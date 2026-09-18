/** 계약 테스트 — specs/002-intent-specify-spec/contracts/openapi.yaml 대조 */
import { describe, it, expect, beforeAll } from 'vitest';
import request from 'supertest';
import { REGION_COUNT } from '../../src/config.js';

process.env.START_SERVER = '0';
const { app } = await import('../../src/server.js');

const GRADES = ['낮음', '보통', '높음', '매우높음'];
let date = '';
let regionCd = '';

beforeAll(async () => {
  const r = await request(app).get('/api/risk/daily');
  date = r.body.targetDate;
  regionCd = r.body.items[0].regionCd;
});

describe('GET /api/risk/daily', () => {
  it(`항상 ${REGION_COUNT}행을 반환한다 (SC-006 — 누락 지역 없음)`, async () => {
    const r = await request(app).get('/api/risk/daily').expect(200);
    expect(r.body.items).toHaveLength(REGION_COUNT);
  });

  it('freshness 를 포함한다 (SC-004)', async () => {
    const r = await request(app).get('/api/risk/daily').expect(200);
    expect(r.body.freshness).toBeTruthy();
    expect(['최신', '최신아님', '폴백']).toContain(r.body.freshness.status);
  });

  it('모든 행이 등급 또는 데이터없음 상태로 확정된다', async () => {
    const r = await request(app).get('/api/risk/daily').expect(200);
    for (const it of r.body.items) {
      const settled = GRADES.includes(it.riskGrade) || it.dataStatus === '데이터없음';
      expect(settled, `${it.regionCd} 상태 미확정`).toBe(true);
    }
  });

  it('위험 순으로 정렬된다 (FR-014)', async () => {
    const r = await request(app).get('/api/risk/daily').expect(200);
    const vals = r.body.items.filter((i: any) => i.expectedCount != null).map((i: any) => Number(i.expectedCount));
    for (let i = 1; i < vals.length; i++) expect(vals[i]).toBeLessThanOrEqual(vals[i - 1]);
  });

  it('정렬 기준을 바꿀 수 있다 (US2 시나리오 3)', async () => {
    const r = await request(app).get('/api/risk/daily?sortBy=probability').expect(200);
    expect(r.body.sortBy).toBe('probability');
    expect(r.body.items).toHaveLength(REGION_COUNT);
  });

  it('잘못된 date 는 400', () => request(app).get('/api/risk/daily?date=2026-13-99').expect(400));
});

describe('GET /api/risk/{regionCd}/{date}', () => {
  it('기여 요인을 문장으로 최소 1개 제시한다 (FR-015 · FR-021)', async () => {
    const r = await request(app).get(`/api/risk/${encodeURIComponent(regionCd)}/${date}`).expect(200);
    expect(Array.isArray(r.body.factors)).toBe(true);
    expect(r.body.factors.length).toBeGreaterThan(0);
    for (const f of r.body.factors) expect(typeof f.sentenceKo).toBe('string');
  });

  it('includeInputs=true 면 입력 전량과 관측가능성 등급을 반환한다 (SC-005 · SC-016)', async () => {
    const r = await request(app)
      .get(`/api/risk/${encodeURIComponent(regionCd)}/${date}?includeInputs=true`).expect(200);
    expect(r.body.inputs).toBeTruthy();
    const grades = Object.values(r.body.inputs.featureGrade ?? {});
    expect(grades.length).toBeGreaterThan(0);
    expect(grades.every((g) => g === 'A' || g === 'B')).toBe(true);   // C 등급은 입력 금지
  });

  it('없는 지역은 404', () =>
    request(app).get(`/api/risk/${encodeURIComponent('없는곳|없는군')}/${date}`).expect(404));
});

describe('GET /api/verification/summary', () => {
  it('오경보·미탐지·캘리브레이션을 집계한다 (FR-019 · SC-008 · SC-011)', async () => {
    const r = await request(app)
      .get('/api/verification/summary?from=2023-01-01&to=2023-03-31').expect(200);
    for (const k of ['hit', 'falseAlarm', 'miss', 'correctReject']) expect(typeof r.body[k]).toBe('number');
    expect(Object.keys(r.body.calibration).length).toBeGreaterThan(0);
    expect(typeof r.body.zScore).toBe('number');
  });
  it('from·to 누락은 400', () => request(app).get('/api/verification/summary').expect(400));
});

describe('GET /api/risk/high-days', () => {
  it('시군구별 고위험 일수를 집계한다 (FR-020)', async () => {
    const r = await request(app).get('/api/risk/high-days?from=2023-01-01&to=2023-12-31').expect(200);
    expect(r.body.length).toBe(REGION_COUNT);
    expect(Number(r.body[0].highDays)).toBeGreaterThanOrEqual(Number(r.body.at(-1).highDays));
  });
});

describe('GET /api/allocation/proposal', () => {
  it('비율 모드로 배분안을 산출한다 (research.md R-2)', async () => {
    const r = await request(app).get(`/api/allocation/proposal?date=${date}`).expect(200);
    expect(['ratio', 'absolute']).toContain(r.body.mode);
    const sum = r.body.items.reduce((s: number, i: any) => s + Number(i.recommendedRatio), 0);
    expect(sum).toBeCloseTo(1, 5);                       // 비율 합은 1
    expect(typeof r.body.baselineUnmetRisk).toBe('number');  // SC-015 비교 대상
  });

  it('각 배정에 근거를 붙인다 (FR-037)', async () => {
    const r = await request(app).get(`/api/allocation/proposal?date=${date}`).expect(200);
    expect(Array.isArray(r.body.items[0].rationale)).toBe(true);
  });
});

describe('POST /api/allocation/adjustments', () => {
  it('필수 필드 누락은 400', () =>
    request(app).post('/api/allocation/adjustments').send({ targetDate: date }).expect(400));

  // 배분안은 조회할 때 DB 에 남기지 않는다. 정책값(α·δ)을 화면에서 바꾸게 되면서
  // 슬라이더 한 칸마다 250행이 쌓이기 때문이다. 그래서 저장 요청은 proposalId 가
  // 아니라 '어느 날짜를 어떤 정책값으로 봤는지' 를 넘기고, 서버가 그 시점에
  // 다시 계산해 권장안과 조정안을 함께 기록한다.
  it('조정안을 권장안·사유·정책값과 함께 보존한다 (FR-039 · SC-009)', async () => {
    const p = await request(app).get(`/api/allocation/proposal?date=${date}`).expect(200);
    const r = await request(app).post('/api/allocation/adjustments').send({
      targetDate: date, policy: { alpha: 0.7, delta: 50 },
      authorId: 'contract-test', authorRole: '지휘',
      reason: '계약 테스트', items: [{ regionCd: p.body.items[0].regionCd, adjustedHeadcount: 7 }],
    }).expect(201);
    expect(r.body.adjustmentId).toBeGreaterThan(0);
    expect(r.body.reason).toBe('계약 테스트');
    expect(r.body.items[0].adjustedHeadcount).toBe(7);
    // 그때 쓴 정책값이 같이 남아야 나중에 "왜 이 안이 나왔나" 를 되짚을 수 있다.
    expect(r.body.policy?.alpha).toBe(0.7);
    expect(r.body.policy?.delta).toBe(50);
  });

  it('시도별 총량을 보존하고 최소 유지 비율을 지킨다', async () => {
    const r = await request(app)
      .get(`/api/allocation/proposal?date=${date}&alpha=0.7&delta=50`).expect(200);
    expect(r.body.mode).toBe('absolute');
    const bySido = new Map<string, { n: number; x: number }>();
    for (const it of r.body.items) {
      // 최소 유지 — x ≥ α·N (부동소수점 여유 1e-6)
      expect(it.recommendedHeadcount).toBeGreaterThanOrEqual(it.currentStaff * 0.7 - 1e-6);
      // 하루 조정 상한 — |x − N| ≤ δ
      expect(Math.abs(it.changeHeadcount)).toBeLessThanOrEqual(50 + 1e-6);
      const s = bySido.get(it.sido) ?? { n: 0, x: 0 };
      s.n += Number(it.currentStaff); s.x += Number(it.recommendedHeadcount);
      bySido.set(it.sido, s);
    }
    // 인력은 시도 경계를 넘지 못한다 — 시도마다 총량이 그대로여야 한다
    for (const [sido, v] of bySido) expect(Math.abs(v.x - v.n), sido).toBeLessThan(1e-6);
  });
});

describe('GET /api/ops/models', () => {
  it('활성 모델의 학습 구간과 피처 등급을 노출한다 (FR-043 · SC-016 · R-3)', async () => {
    const r = await request(app).get('/api/ops/models').expect(200);
    const active = r.body.find((m: any) => m.isActive);
    expect(active).toBeTruthy();
    expect(active.trainDataRange.from).toBeTruthy();
    expect(active.trainDataRange.to).toBeTruthy();
    expect(active.acceptancePassed).toBe(true);
  });
});

describe('GET /api/ops/batch-status', () => {
  it('배치 실패를 조용히 넘기지 않는다 (FR-025)', async () => {
    const r = await request(app).get('/api/ops/batch-status').expect(200);
    expect(['성공', '부분실패', '실패', '폴백', '실행중']).toContain(r.body.status);
    expect(Array.isArray(r.body.failureDetail)).toBe(true);
  });
});
