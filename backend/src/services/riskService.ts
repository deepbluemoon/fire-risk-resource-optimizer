import { q, q1 } from '../db/pool.js';
import { config } from '../config.js';
import { todaySeoul } from '../util/date.js';

const MV = config.modelVersion;

export interface Freshness {
  status: '최신' | '최신아님' | '폴백';
  predictedAt: string | null;
  forecastIssuedAt: string | null;
  fallbackSource: string | null;
}

/** 대상일의 최신 배치 실행 상태 → SC-004 (데이터 최신성 100% 인지) */
export async function freshness(date: string): Promise<Freshness> {
  const run = await q1<any>(
    `SELECT started_at, finished_at, status, fallback_source, forecast_fetched_at
       FROM ops_batch_run WHERE target_date=? ORDER BY run_id DESC LIMIT 1`, [date]);
  if (!run) return { status: '최신아님', predictedAt: null, forecastIssuedAt: null, fallbackSource: null };
  const status = run.fallback_source ? '폴백' : (run.status === '성공' ? '최신' : '최신아님');
  return {
    status,
    predictedAt: run.finished_at ?? run.started_at ?? null,
    forecastIssuedAt: run.forecast_fetched_at ?? null,
    fallbackSource: run.fallback_source ?? null,
  };
}

/** 날짜를 지정하지 않았을 때 보여줄 날. **오늘을 넘지 않는 가장 최근 날짜**다.
 *
 *  그냥 MAX(target_date) 였다. 배치가 예보로 앞으로 일주일치를 미리 계산해 두게
 *  되면서 그 값이 D+6 을 가리키게 됐다 — 첫 화면이 '오늘의 위험도' 라는 제목으로
 *  다음 주 예보를 보여주는 셈이다. 선계산은 오늘 배치가 실패해도 화면이 비지 않게
 *  하려는 안전망이지 기본 조회일을 미래로 옮기려는 것이 아니므로, 여기서 오늘로
 *  천장을 씌운다. 미래분만 있는 특수한 경우(첫 적재 직후)에만 가장 가까운 날로
 *  내려간다 — 그때도 빈 화면보다는 낫다. */
export async function latestDate(): Promise<string | null> {
  const today = todaySeoul();
  const r = await q1<any>(
    `SELECT MAX(target_date) d FROM pred_daily WHERE is_current=1 AND target_date <= ?`,
    [today]);
  if (r?.d) return String(r.d);
  const f = await q1<any>(`SELECT MIN(target_date) d FROM pred_daily WHERE is_current=1`);
  return f?.d ? String(f.d) : null;
}

/** 예측이 실제로 있는 날짜들.
 *
 *  이 서비스는 오늘만 보는 것이 아니다. 배치가 Open-Meteo 예보로 앞으로 며칠치를
 *  미리 계산해 두고, 지난 날짜도 그대로 남긴다. 그런데 화면은 아무 날짜나 고르게
 *  두고 없는 날에는 빈 표를 보여줬다 — 사용자는 고장으로 읽는다.
 *  실제로 있는 날짜를 알려줘서 달력이 그 범위만 열리게 한다.
 */
export async function availableDates() {
  const rows = await q<any>(
    `SELECT target_date AS d FROM pred_daily WHERE is_current=1
      GROUP BY target_date ORDER BY target_date`);
  const dates = rows.map((r: any) => String(r.d));
  if (!dates.length) return { dates: [], min: null, max: null, recentMin: null };

  // 달력의 기본 범위는 '최근 묶음' 으로 잡는다. 검증용으로 넣어둔 2023 년까지
  // 한꺼번에 열어두면 오늘 근처를 고르기가 오히려 불편하다.
  let recentMin = dates[dates.length - 1];
  for (let i = dates.length - 1; i > 0; i--) {
    const cur = new Date(dates[i]).getTime();
    const prev = new Date(dates[i - 1]).getTime();
    if (cur - prev > 3 * 86400000) break;      // 3일 넘게 비면 다른 묶음으로 본다
    recentMin = dates[i - 1];
  }
  return { dates, min: dates[0], max: dates[dates.length - 1], recentMin };
}

const ORDER: Record<string, string> = {
  count: 'p.expected_count DESC',
  // 미적재 지표는 NULL 이다 — 정렬 시 뒤로 민다 (NULL 을 0 으로 오해하지 않게)
  damage: '(p.expected_damage_load IS NULL), p.expected_damage_load DESC',
  probability: 'p.occur_probability DESC',
  spread: '(p.spread_probability IS NULL), p.spread_probability DESC',
  // M4 는 '참고용' 등급이다 — 정렬 기준으로 노출하되 화면이 등급을 함께 알린다
  longburn: '(p.longburn_probability IS NULL), p.longburn_probability DESC',
};

/** 지도용 — 251 시군구의 청사 좌표와 그날의 위험 등급.
 *
 *  시군구 경계 GeoJSON 은 저장소에 없다(수십 MB). DB 에는 청사 좌표가 있으므로
 *  그 점을 찍어 전국 분포를 보여준다. 경계 폴리곤이 확보되면 같은 응답에
 *  geometry 만 얹으면 된다 — 화면 계약은 그대로 둔다.
 */
export async function mapPoints(date: string) {
  return q(
    `SELECT p.region_cd  AS regionCd,
            r.sido, r.sigungu,
            c.위도       AS lat,
            c.경도       AS lon,
            p.risk_grade AS riskGrade,
            p.occur_probability AS occurProbability,
            p.expected_count    AS expectedCount,
            p.data_status       AS dataStatus
       FROM pred_daily p
       JOIN ref_region_profile r ON r.region_cd = p.region_cd
       -- 두 테이블의 collation 이 다르다(utf8mb4_uca1400_ai_ci vs utf8mb4_unicode_ci).
       -- 그대로 비교하면 'Illegal mix of collations' 로 쿼리가 죽는다.
       LEFT JOIN tbl_sido_lonlat_std05 c
              ON REPLACE(TRIM(TRAILING '청' FROM REPLACE(c.기관명, ' ', '')), ' ', '')
                 COLLATE utf8mb4_unicode_ci
                 = REPLACE(r.sigungu, ' ', '') COLLATE utf8mb4_unicode_ci
      WHERE p.target_date = ? AND p.is_current = 1
      GROUP BY p.region_cd
      ORDER BY r.sido, r.sigungu`, [date]);
}


/** 어떤 정렬 기준이 실제로 쓸 수 있는지 — 미적재 모델을 화면이 있는 척하지 않게 한다 */
export async function availableMetrics(date: string) {
  const r = await q1<any>(
    `SELECT SUM(expected_count IS NOT NULL) c, SUM(occur_probability IS NOT NULL) p,
            SUM(expected_damage_load IS NOT NULL) d, SUM(spread_probability IS NOT NULL) s,
            SUM(longburn_probability IS NOT NULL) l
       FROM pred_daily WHERE target_date=? AND is_current=1`, [date]);
  return {
    count: Number(r?.c ?? 0) > 0,
    probability: Number(r?.p ?? 0) > 0,
    damage: Number(r?.d ?? 0) > 0,
    spread: Number(r?.s ?? 0) > 0,
    longburn: Number(r?.l ?? 0) > 0,
  };
}

export async function dailyList(opts: {
  date: string; sido?: string; minGrade?: string; sortBy?: string;
}) {
  const grades = ['낮음', '보통', '높음', '매우높음'];
  const params: any[] = [opts.date];
  let where = 'p.target_date=? AND p.is_current=1';
  if (opts.sido) { where += ' AND r.sido=?'; params.push(opts.sido); }
  if (opts.minGrade) {
    const idx = grades.indexOf(opts.minGrade);
    if (idx > 0) { where += ` AND p.risk_grade IN (${grades.slice(idx).map(() => '?').join(',')})`; params.push(...grades.slice(idx)); }
  }
  const order = ORDER[opts.sortBy ?? 'count'] ?? ORDER.count;
  return q(
    `SELECT p.region_cd AS regionCd, r.sido, r.sigungu, p.risk_rank AS riskRank,
            p.occur_probability AS occurProbability, p.expected_count AS expectedCount,
            p.risk_grade AS riskGrade, p.expected_damage_load AS expectedDamageLoad,
            p.spread_probability AS spreadProbability, p.longburn_probability AS longburnProbability,
            p.confidence, p.data_status AS dataStatus
       FROM pred_daily p JOIN ref_region_profile r ON r.region_cd=p.region_cd
      WHERE ${where}
      ORDER BY (p.data_status='데이터없음'), ${order}, r.sido, r.sigungu`, params);
}

export async function detail(regionCd: string, date: string, includeInputs: boolean) {
  const row = await q1<any>(
    // 청사 좌표까지 함께 준다 — 상세 화면이 그 지역의 현재 날씨를 부르는 데 쓴다.
    // collation 이 달라 그냥 비교하면 'Illegal mix of collations' 로 죽는다.
    `SELECT p.*, r.sido, r.sigungu, s.assign_method, c.cluster_label,
            g.위도 AS lat, g.경도 AS lon
       FROM pred_daily p
       JOIN ref_region_profile r ON r.region_cd=p.region_cd
       LEFT JOIN ref_station_map s ON s.region_cd=p.region_cd
       LEFT JOIN ref_region_cluster c ON c.region_cd=p.region_cd AND c.model_version=?
       LEFT JOIN tbl_sido_lonlat_std05 g
              ON REPLACE(TRIM(TRAILING '청' FROM REPLACE(g.기관명, ' ', '')), ' ', '')
                 COLLATE utf8mb4_unicode_ci
                 = REPLACE(r.sigungu, ' ', '') COLLATE utf8mb4_unicode_ci
      WHERE p.region_cd=? AND p.target_date=? AND p.is_current=1
      LIMIT 1`, [MV, regionCd, date]);
  if (!row) return null;

  const out: any = {
    regionCd: row.region_cd, sido: row.sido, sigungu: row.sigungu,
    targetDate: row.target_date, riskRank: row.risk_rank,
    occurProbability: row.occur_probability, expectedCount: row.expected_count,
    riskGrade: row.risk_grade, expectedDamageLoad: row.expected_damage_load,
    spreadProbability: row.spread_probability, longburnProbability: row.longburn_probability,
    confidence: row.confidence, dataStatus: row.data_status,
    clusterLabel: row.cluster_label,
    lat: row.lat == null ? null : Number(row.lat),
    lon: row.lon == null ? null : Number(row.lon),
    stationAssignMethod: row.assign_method ?? '관내',
    factors: parseJson(row.top_factors) ?? [],
    modelVersions: { M0: row.source_model_version },
    inputs: null,
  };
  if (includeInputs) {
    const snap = await q1<any>(
      `SELECT features, feature_grade, station_id, forecast_issued_at
         FROM pred_feature_snapshot WHERE region_cd=? AND target_date=? AND run_id=?`,
      [regionCd, date, row.run_id]);
    if (snap) {
      out.inputs = {
        features: parseJson(snap.features), featureGrade: parseJson(snap.feature_grade),
        forecastIssuedAt: snap.forecast_issued_at,
      };
      out.stationId = snap.station_id;
    }
  }
  return out;
}

export async function highDays(from: string, to: string, sortBy = 'high') {
  const order = sortBy === 'damage' ? 'damageSum DESC' : 'highDays DESC';
  return q(
    `SELECT region_cd AS regionCd, sido, sigungu,
            SUM(is_high) AS highDays, COUNT(*) AS totalDays,
            SUM(is_high)/COUNT(*) AS ratio,
            SUM(COALESCE(expected_damage_load,0)) AS damageSum
       FROM v_pred_high_days
      WHERE target_date BETWEEN ? AND ?
      GROUP BY region_cd, sido, sigungu
      ORDER BY ${order}`, [from, to]);
}

export async function monthlyProfile(regionCd: string, from: string, to: string) {
  return q(
    `SELECT DATE_FORMAT(target_date,'%Y-%m') AS ym, SUM(is_high) AS highDays, COUNT(*) AS totalDays
       FROM v_pred_high_days
      WHERE region_cd=? AND target_date BETWEEN ? AND ?
      GROUP BY ym ORDER BY ym`, [regionCd, from, to]);
}

export function parseJson(v: any) {
  if (v == null) return null;
  if (typeof v === 'object') return v;
  try { return JSON.parse(v); } catch { return null; }
}
