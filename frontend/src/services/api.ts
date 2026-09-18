/** openapi.yaml 기반 타입과 fetch 래퍼. 모든 경로는 /api 프록시를 지난다. */

export type RiskGrade = '낮음' | '보통' | '높음' | '매우높음';
export type DataStatus = '정상' | '최신아님' | '데이터없음';
export type Confidence = '정상' | '대리지점' | '폴백';

export interface Freshness {
  status: '최신' | '최신아님' | '폴백';
  predictedAt: string | null;
  forecastIssuedAt: string | null;
  fallbackSource: string | null;
}

export interface Factor {
  feature: string; value: number | null;
  direction: '상승' | '하락'; magnitude: number; sentenceKo: string;
}

export interface RiskItem {
  regionCd: string; sido: string; sigungu: string;
  riskRank: number | null; occurProbability: number | null; expectedCount: number | null;
  riskGrade: RiskGrade | null; expectedDamageLoad: number | null;
  spreadProbability: number | null; longburnProbability: number | null;
  confidence: Confidence; dataStatus: DataStatus;
}

export interface MetricAvailability { count: boolean; probability: boolean; damage: boolean; spread: boolean; }

export interface RiskDaily {
  targetDate: string; freshness: Freshness; sortBy: string;
  available: MetricAvailability; items: RiskItem[];
}

export interface RiskDetail extends RiskItem {
  targetDate: string; clusterLabel: string | null; stationAssignMethod: string;
  /** 청사 좌표 — 이 지역의 현재 날씨를 부르는 데 쓴다 */
  lat: number | null; lon: number | null;
  factors: Factor[]; modelVersions: Record<string, string>; stationId?: string | null;
  inputs: { features: Record<string, unknown>; featureGrade: Record<string, 'A' | 'B'>;
            forecastIssuedAt: string | null } | null;
}

export interface GradeRef {
  grade: RiskGrade; probLower: number; probUpper: number;
  colorToken: string; descriptionKo: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' }, ...init,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(body?.message ?? `요청 실패 (${res.status})`) as Error & { body?: unknown; status?: number };
    err.body = body; err.status = res.status;
    throw err;
  }
  return body as T;
}

const qs = (o: Record<string, unknown>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&');

export const api = {
  riskDaily: (p: { date?: string; sido?: string; minGrade?: string; sortBy?: string }) =>
    req<RiskDaily>(`/risk/daily${qs(p) ? `?${qs(p)}` : ''}`),

  riskDetail: (regionCd: string, date: string, includeInputs = false) =>
    req<RiskDetail>(`/risk/${encodeURIComponent(regionCd)}/${date}?includeInputs=${includeInputs}`),

  cause: (regionCd: string, date: string) =>
    req<CauseResponse>(`/cause/${encodeURIComponent(regionCd)}/${date}`),

  causeSignal: () => req<CauseSignal>('/cause/_signal'),

  /** 배분안. 정책값(α·β·δ)에 따라 답이 달라지므로 함께 넘긴다.
   *  δ 는 '무제한' 을 문자열로 보낸다 — JSON 에 Infinity 가 없기 때문이다. */
  proposal: (date?: string, opt?: { alpha?: number; beta?: number; delta?: number | '무제한'; sido?: string }) =>
    req<any>(`/allocation/proposal?${qs({ date, ...(opt ?? {}) })}`),

  constraints: () => req<{ mode: 'ratio' | 'absolute'; items: any[] }>('/allocation/constraints'),

  adjustments: (from?: string, to?: string) =>
    req<any[]>(`/allocation/adjustments${qs({ from, to }) ? `?${qs({ from, to })}` : ''}`),

  saveAdjustment: (body: unknown) =>
    req<any>('/allocation/adjustments', { method: 'POST', body: JSON.stringify(body) }),

  verifyHistory: (from: string, to: string, regionCd?: string) =>
    req<any[]>(`/verification/history?${qs({ from, to, regionCd })}`),

  verifySummary: (from: string, to: string, sido?: string) =>
    req<any>(`/verification/summary?${qs({ from, to, sido })}`),

  /** 지도용 — 251 시군구 좌표 + 그날 위험 등급 */
  mapPoints: (date: string) => req<any[]>(`/risk/map/${date}`),

  /** 예측이 있는 날짜 — 달력 범위를 이걸로 제한한다 */
  availableDates: () => req<{
    dates: string[]; min: string | null; max: string | null; recentMin: string | null;
  }>('/risk/dates'),

  /** 일상어 정확도 — 통계 용어 대신 쓴다 */
  accuracy: (from: string, to: string, sido?: string) =>
    req<any>(`/verification/accuracy?${qs({ from, to, sido })}`),

  batchStatus: () => req<any>('/ops/batch-status'),
  batchRuns: () => req<any[]>('/ops/batch-runs'),
  models: () => req<any[]>('/ops/models'),
  regions: () => req<any[]>('/ops/regions'),
  grades: () => req<GradeRef[]>('/ops/grades'),
};

export interface CauseItem {
  rank: number; causeClass: string; probability: number;
  liftVsBase: number | null; anomaly: boolean;
}

/** 불합격 모델의 산출을 화면이 정상값처럼 보이지 않게 하려고 등급과 근거를 함께 받는다. */
export interface CauseCaveatDetail {
  headline: string;
  lead: string;
  reasons: string[];
  warning: string;
  trustworthy: string;
}

export interface CauseModelStatus {
  loaded: boolean; passed: boolean; modelVersion?: string;
  caveat: string | null; metrics: Record<string, unknown> | null;
  reliability?: '정상' | '참고용' | '미적재';
  caveatDetail?: CauseCaveatDetail | null;
}

export interface CauseResponse {
  items: CauseItem[]; model: CauseModelStatus; unavailable: string | null;
}

export interface CauseSignal extends CauseModelStatus {
  lifts: Array<{ causeClass: string; lift: number; useful: boolean }>;
}

/** 모델 이름 — 화면에는 코드(M1)만 쓰지 않고 '무엇을 하는지 + (코드)' 로 보여준다.
 *  사용자는 M1 이 무엇인지 알 이유가 없고, 코드는 담당자가 문의할 때만 필요하다. */
export const MODEL_LABEL: Record<string, string> = {
  M0: '지역·계절 기본값 (M0)',
  M1: '화재 발생 예측 (M1)',
  M2a: '불길 번짐 예측 (M2a)',
  M2b: '피해 규모 예측 (M2b)',
  M3: '원인 이상 신호 (M3)',
  M4: '진화 장기화 예측 (M4)',
  M5: '화재 발생 확률 (M5)',
};

/** 모델 버전 문자열(m1-gbm-20260901)에서 이름을 찾는다. */
export function modelLabel(idOrVersion: string | null | undefined): string {
  if (!idOrVersion) return '—';
  const s = String(idOrVersion);
  if (MODEL_LABEL[s]) return MODEL_LABEL[s];
  const hit = Object.keys(MODEL_LABEL)
    .sort((a, b) => b.length - a.length)
    .find((k) => s.toLowerCase().startsWith(k.toLowerCase() + '-'));
  return hit ? MODEL_LABEL[hit] : s;
}

/** 채우기·표식용 — 채도를 살린 선명한 색 */
export const GRADE_COLOR: Record<string, string> = {
  '낮음': 'var(--g-low)', '보통': 'var(--g-mid)',
  '높음': 'var(--g-high)', '매우높음': 'var(--g-critical)',
};

/** 글자용 — 흰 바탕에서 읽히도록 누른 색. 어두운 화면에서는 위와 같아진다.
 *  한 색으로 채우기와 글자를 다 하려 들면 '읽히지만 탁한' 절충이 된다. */
export const GRADE_INK: Record<string, string> = {
  '낮음': 'var(--g-low-ink)', '보통': 'var(--g-mid-ink)',
  '높음': 'var(--g-high-ink)', '매우높음': 'var(--g-critical-ink)',
};

export const GRADE_ORDER: RiskGrade[] = ['매우높음', '높음', '보통', '낮음'];

export const pct = (v: number | null | undefined, d = 1) =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(d)}%`;
export const num = (v: number | null | undefined, d = 3) =>
  v == null ? '—' : Number(v).toFixed(d);

/** '0.36건' 은 일상어가 아니다. 며칠에 한 번꼴인지 함께 알려준다.
 *  건수는 하루 단위 기대값이므로 1/건수 가 평균 간격(일)이 된다. */
export function everyHowOften(perDay: number | null | undefined): string | null {
  const v = Number(perDay);
  if (!perDay || !isFinite(v) || v <= 0) return null;
  if (v >= 1) return `하루 평균 ${v.toFixed(1)}건`;
  const days = 1 / v;
  if (days < 1.5) return '거의 매일';
  if (days < 30) return `약 ${Math.round(days)}일에 한 번꼴`;
  if (days < 365) return `약 ${Math.round(days / 30)}달에 한 번꼴`;
  return `약 ${(days / 365).toFixed(1)}년에 한 번꼴`;
}
/** 이 서비스의 기준 시간대. 사용자·서버가 어디에 있든 한국 날짜로 본다. */
export const TZ = 'Asia/Seoul';

/** 서울 기준 YYYY-MM-DD.
 *  toISOString() 은 UTC 라 00~09시 사이에 '어제' 를 돌려준다 — 매일 아침 9시간 동안
 *  하루 전 예측이 보이는 버그가 됐다. en-CA 로케일이 YYYY-MM-DD 를 준다. */
const ymd = (d: Date) =>
  new Intl.DateTimeFormat('en-CA', { timeZone: TZ }).format(d);

export const today = () => ymd(new Date());
export const daysAgo = (n: number) => ymd(new Date(Date.now() - n * 86400000));

/** 서울 기준 일시 표기. 브라우저가 해외에 있어도 한국 시각으로 보여준다. */
export const dateTimeKo = (v: string | number | Date | null | undefined) =>
  v == null ? '—'
    : new Date(v).toLocaleString('ko-KR', { timeZone: TZ, dateStyle: 'medium', timeStyle: 'short' });
