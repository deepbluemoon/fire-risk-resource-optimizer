import { Router } from 'express';
import { q, q1 } from '../db/pool.js';
import { parseJson } from '../services/riskService.js';
import { todaySeoul } from '../util/date.js';

export const opsRouter = Router();

/** FR-024 · FR-025 · SC-004 — 실패를 조용히 넘기지 않는다.
 *
 *  '가장 최근 실행'(run_id DESC) 이 아니라 **오늘을 넘지 않는 가장 최근 대상일**의
 *  실행을 낸다. 배치가 오늘부터 7일치를 미리 계산하게 되면서 run_id 최대값은 늘
 *  D+6 건이 됐고, 운영 화면이 "가장 최근 계산 · 2026-09-10" 을 띄웠다. 운영자가
 *  이 화면에서 확인하려는 것은 하나다 — **오늘 것이 제대로 만들어졌는가.**
 *  미래분 실행 기록이 그 답을 가리면 안 된다. */
opsRouter.get('/batch-status', async (_req, res, next) => {
  try {
    const r = await q1<any>(
      `SELECT run_id AS runId, target_date AS targetDate, started_at AS startedAt,
              finished_at AS finishedAt, status, regions_ok AS regionsOk,
              regions_failed AS regionsFailed, failure_detail AS failureDetail,
              fallback_source AS fallbackSource, forecast_fetched_at AS forecastFetchedAt,
              drift_alerts AS driftAlerts
         FROM ops_batch_run
        WHERE target_date <= ?
        ORDER BY target_date DESC, run_id DESC LIMIT 1`, [todaySeoul()]);
    if (!r) return res.json({ runId: null, status: '실패', regionsOk: 0, regionsFailed: 0,
      failureDetail: [{ regionCd: null, stage: '배치', reason: '실행 기록이 없습니다' }], driftAlerts: [] });
    res.json({ ...r, failureDetail: parseJson(r.failureDetail) ?? [], driftAlerts: parseJson(r.driftAlerts) ?? [] });
  } catch (e) { next(e); }
});

opsRouter.get('/batch-runs', async (_req, res, next) => {
  try {
    res.json(await q(
      `SELECT run_id AS runId, target_date AS targetDate, status, regions_ok AS regionsOk,
              regions_failed AS regionsFailed, fallback_source AS fallbackSource, finished_at AS finishedAt
         FROM ops_batch_run ORDER BY run_id DESC LIMIT 50`));
  } catch (e) { next(e); }
});

/** FR-043 · SC-016 — 입력 변수와 관측가능성이 문서화되어 있음을 확인할 수 있다 */
opsRouter.get('/models', async (_req, res, next) => {
  try {
    const rows = await q<any>(
      `SELECT model_id AS modelId, model_version AS modelVersion, algorithm,
              train_data_range AS trainDataRange, feature_list AS features,
              cv_metrics AS cvMetrics, holdout_metrics AS holdoutMetrics,
              acceptance_passed AS acceptancePassed, code_commit AS codeCommit,
              is_active AS isActive, created_at AS createdAt
         FROM ops_model_registry ORDER BY is_active DESC, model_id`);
    res.json(rows.map((r) => ({
      ...r,
      acceptancePassed: !!r.acceptancePassed,
      isActive: !!r.isActive,
      trainDataRange: parseJson(r.trainDataRange),
      features: parseJson(r.features) ?? [],
      cvMetrics: parseJson(r.cvMetrics),
      holdoutMetrics: parseJson(r.holdoutMetrics),
    })));
  } catch (e) { next(e); }
});

opsRouter.get('/regions', async (_req, res, next) => {
  try {
    res.json(await q(
      `SELECT p.region_cd AS regionCd, p.sido, p.sigungu, p.population, p.pop_density AS popDensity,
              p.forest_ratio AS forestRatio, p.avg_station_distance_km AS avgStationDistanceKm,
              c.cluster_label AS clusterLabel, s.assign_method AS assignMethod
         FROM ref_region_profile p
         LEFT JOIN ref_region_cluster c ON c.region_cd=p.region_cd
         LEFT JOIN ref_station_map s ON s.region_cd=p.region_cd
        ORDER BY p.sido, p.sigungu`));
  } catch (e) { next(e); }
});

opsRouter.get('/grades', async (_req, res, next) => {
  try {
    // ref_risk_grade 에는 모델 버전마다 한 벌씩 들어 있다(M0 용·M1 용). 버전을
    // 안 걸면 등급이 두 번씩 나온다. 실제로 예측에 쓰인 버전만 내보낸다.
    res.json(await q(
      `SELECT g.grade, g.prob_lower AS probLower, g.prob_upper AS probUpper,
              g.color_token AS colorToken, g.description_ko AS descriptionKo,
              g.model_version AS modelVersion
         FROM ref_risk_grade g
        WHERE g.model_version = (
                SELECT source_model_version FROM pred_daily
                 WHERE is_current = 1 AND source_model_version IS NOT NULL
                 ORDER BY target_date DESC LIMIT 1)
        ORDER BY g.sort_order`));
  } catch (e) { next(e); }
});

opsRouter.get('/health', async (_req, res) => {
  try {
    await q1('SELECT 1 AS ok');
    res.json({ status: 'ok', db: 'up', ts: new Date().toISOString() });
  } catch (e: any) {
    res.status(503).json({ status: 'degraded', db: 'down', error: String(e?.message ?? e) });
  }
});
