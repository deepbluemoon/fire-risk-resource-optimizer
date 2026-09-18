import { Router } from 'express';
import * as svc from '../services/riskService.js';
import { badRequest, notFound } from '../middleware/errors.js';
import { isValidDate as isDate, todaySeoul } from '../util/date.js';

export const riskRouter = Router();


/** FR-014 — 항상 251행. 예측 불가 지역도 '데이터없음' 으로 포함한다 (SC-006) */
riskRouter.get('/daily', async (req, res, next) => {
  try {
    const date = (req.query.date as string) || (await svc.latestDate()) || todaySeoul();
    if (!isDate(date)) throw badRequest('date 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    const [items, fresh, available] = await Promise.all([
      svc.dailyList({
        date, sido: req.query.sido as string | undefined,
        minGrade: req.query.minGrade as string | undefined,
        sortBy: req.query.sortBy as string | undefined,
      }),
      svc.freshness(date),
      svc.availableMetrics(date),
    ]);
    res.json({ targetDate: date, freshness: fresh, available,
               sortBy: req.query.sortBy ?? 'count', items });
  } catch (e) { next(e); }
});

/** 예측이 있는 날짜 — 달력이 이 범위만 열리게 한다.
 *  `/:regionCd/:date` 보다 먼저 선언해야 'dates' 가 지역코드로 잡히지 않는다. */
riskRouter.get('/dates', async (_req, res, next) => {
  try {
    res.json(await svc.availableDates());
  } catch (e) { next(e); }
});

/** 지도용 — 251 시군구의 청사 좌표 + 그날의 위험 등급.
 *  `/:regionCd/:date` 보다 먼저 선언해야 한다. 뒤에 두면 'map' 이 regionCd 로 잡힌다. */
riskRouter.get('/map/:date', async (req, res, next) => {
  try {
    const { date } = req.params;
    if (!isDate(date)) throw badRequest('date 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.mapPoints(date));
  } catch (e) { next(e); }
});

/** FR-020 · US6 */
riskRouter.get('/high-days', async (req, res, next) => {
  try {
    const { from, to } = req.query as Record<string, string>;
    if (!isDate(from) || !isDate(to)) throw badRequest('from·to 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.highDays(from, to, req.query.sortBy as string));
  } catch (e) { next(e); }
});

riskRouter.get('/high-days/:regionCd/profile', async (req, res, next) => {
  try {
    const { from, to } = req.query as Record<string, string>;
    if (!isDate(from) || !isDate(to)) throw badRequest('from·to 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.monthlyProfile(decodeURIComponent(req.params.regionCd), from, to));
  } catch (e) { next(e); }
});

/** FR-015 — 상세. includeInputs=true 면 FR-023 · SC-005 재구성용 입력 전량 */
riskRouter.get('/:regionCd/:date', async (req, res, next) => {
  try {
    if (!isDate(req.params.date)) throw badRequest('date 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    const row = await svc.detail(decodeURIComponent(req.params.regionCd), req.params.date,
      req.query.includeInputs === 'true');
    if (!row) throw notFound();
    res.json(row);
  } catch (e) { next(e); }
});
