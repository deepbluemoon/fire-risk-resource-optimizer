/** US2 확산 · US3 원인 계약 테스트 (T066 · T074) */
import { describe, it, expect, beforeAll } from 'vitest';
import request from 'supertest';
import { REGION_COUNT } from '../../src/config.js';

process.env.START_SERVER = '0';
const { app } = await import('../../src/server.js');

/** 기상 입력이 있어 M1·M2a·M3 가 돌아간 날짜를 고른다 */
const MODEL_DATE = '2023-04-03';
let regionCd = '';

beforeAll(async () => {
  const r = await request(app).get(`/api/risk/daily?date=${MODEL_DATE}`);
  regionCd = r.body.items[0].regionCd;
});

describe('확산 (US2)', () => {
  it('모델이 돈 날짜에는 확산 확률이 채워진다', async () => {
    const r = await request(app).get(`/api/risk/daily?date=${MODEL_DATE}`).expect(200);
    expect(r.body.available.spread).toBe(true);
    const withSpread = r.body.items.filter((i: any) => i.spreadProbability != null);
    expect(withSpread.length).toBe(REGION_COUNT);
    for (const i of withSpread) {
      expect(i.spreadProbability).toBeGreaterThanOrEqual(0);
      expect(i.spreadProbability).toBeLessThanOrEqual(1);
    }
  });

  it('기대 피해량이 발생×확산으로 구성된다 (FR-033)', async () => {
    const r = await request(app).get(`/api/risk/daily?date=${MODEL_DATE}`).expect(200);
    for (const i of r.body.items.slice(0, 20)) {
      const expected = Number(i.expectedCount) * (1 + Number(i.spreadProbability));
      expect(Number(i.expectedDamageLoad)).toBeCloseTo(expected, 4);
    }
  });

  it('확산 기준 정렬이 발생 건수 기준과 다른 순위를 낸다 (US2 시나리오 3)', async () => {
    const [byCount, bySpread] = await Promise.all([
      request(app).get(`/api/risk/daily?date=${MODEL_DATE}&sortBy=count`).expect(200),
      request(app).get(`/api/risk/daily?date=${MODEL_DATE}&sortBy=spread`).expect(200),
    ]);
    const a = byCount.body.items.slice(0, 20).map((i: any) => i.regionCd);
    const b = bySpread.body.items.slice(0, 20).map((i: any) => i.regionCd);
    expect(a).not.toEqual(b);   // 자주 나는 곳과 크게 번지는 곳은 다르다
  });

  it('상세에 확산 기여 요인 문장이 붙는다 (FR-034)', async () => {
    const r = await request(app)
      .get(`/api/risk/${encodeURIComponent(regionCd)}/${MODEL_DATE}`).expect(200);
    const spread = r.body.factors.find((f: any) => f.kind === 'spread');
    expect(spread, '확산 요인이 상위 3개에 포함되어야 한다').toBeTruthy();
    expect(spread.sentenceKo).toContain('번질 확률');
  });
});

describe('원인 (US3)', () => {
  // 화면이 '평소보다 눈에 띄게 늘어난 원인' 만 보여주도록 바뀌면서 계약도 바뀌었다.
  // 늘 많은 원인(부주의·전기)까지 상위 3개를 채워 내보내면 매일 같은 세 줄이 떠서
  // 아무 정보도 주지 못한다. 그래서 걸러낸 결과가 0~3개다 — 0개인 날이 정상이다.
  // 고정 3개를 기대하던 옛 계약을 그대로 두면 이 화면 변경이 실패로 보고된다.
  it('평시 대비 배율과 함께 최대 3 후보를 순위대로 반환한다', async () => {
    const r = await request(app)
      .get(`/api/cause/${encodeURIComponent(regionCd)}/${MODEL_DATE}`).expect(200);
    expect(r.body.items.length).toBeLessThanOrEqual(3);
    expect(r.body.items.map((i: any) => i.rank))
      .toEqual(r.body.items.map((_: any, n: number) => n + 1));
    for (const i of r.body.items) {
      expect(typeof i.causeClass).toBe('string');
      expect(i.probability).toBeGreaterThan(0);
      expect(typeof i.anomaly).toBe('boolean');
    }
  });

  it('합격하지 못한 모델은 순위 해석 주의를 함께 내보낸다', async () => {
    const r = await request(app)
      .get(`/api/cause/${encodeURIComponent(regionCd)}/${MODEL_DATE}`).expect(200);
    if (!r.body.model.passed) {
      expect(r.body.model.caveat, '미통과 모델은 caveat 을 반드시 함께 내야 한다').toBeTruthy();
      // 문구 자체가 아니라 '이것만 보고 배치를 정하지 말라' 는 경고가 남아 있는지를 본다.
      // 예전엔 '고정 제시' 라는 특정 표현을 찾았는데, 그 표현은 명칭 정리 때 사라졌다.
      // 문구를 못 박으면 말을 다듬을 때마다 계약이 깨진다.
      expect(r.body.model.caveat).toMatch(/정확도가 충분하지 않|참고용|결정하지 마세요/);
    }
  });

  it('클래스별 신호 강도를 조회할 수 있다', async () => {
    const r = await request(app).get('/api/cause/_signal').expect(200);
    expect(Array.isArray(r.body.lifts)).toBe(true);
    expect(r.body.lifts.length).toBeGreaterThan(0);
    const sorted = r.body.lifts.map((l: any) => l.lift);
    expect([...sorted].sort((a, b) => b - a)).toEqual(sorted);   // 내림차순
  });

  it('예측이 없는 날짜는 사유와 함께 빈 배열을 반환한다 (조용히 실패하지 않는다)', async () => {
    const r = await request(app)
      .get(`/api/cause/${encodeURIComponent(regionCd)}/2019-01-01`).expect(200);
    expect(r.body.items).toEqual([]);
    expect(r.body.unavailable).toBeTruthy();
  });
});

describe('이력·집계 (US5 · US6)', () => {
  it('/verification/history 가 예측과 실제를 대조한다 (FR-018)', async () => {
    const r = await request(app)
      .get('/api/verification/history?from=2023-04-01&to=2023-04-02').expect(200);
    expect(r.body.length).toBeGreaterThan(0);
    for (const x of r.body.slice(0, 10)) {
      expect(['hit', 'false_alarm', 'miss', 'correct_reject']).toContain(x.outcome);
      expect(typeof x.actualCount).toBe('number');
    }
  });

  it(`/risk/high-days 가 ${REGION_COUNT}개 시군구를 고위험 일수 내림차순으로 낸다 (FR-020)`, async () => {
    const r = await request(app)
      .get('/api/risk/high-days?from=2023-01-01&to=2023-12-31').expect(200);
    expect(r.body.length).toBe(REGION_COUNT);
    const v = r.body.map((x: any) => Number(x.highDays));
    for (let i = 1; i < v.length; i++) expect(v[i]).toBeLessThanOrEqual(v[i - 1]);
  });

  it('월별 집중 시기를 분해 조회할 수 있다 (US6 시나리오 2)', async () => {
    const r = await request(app)
      .get(`/api/risk/high-days/${encodeURIComponent(regionCd)}/profile?from=2023-01-01&to=2023-12-31`)
      .expect(200);
    expect(r.body.length).toBe(12);
    expect(r.body[0].ym).toBe('2023-01');
  });
});
