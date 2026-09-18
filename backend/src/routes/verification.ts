import { Router } from 'express';
import * as svc from '../services/verificationService.js';
import { badRequest } from '../middleware/errors.js';
import { isValidDate as isDate } from '../util/date.js';

export const verificationRouter = Router();

/** FR-018 · US5 */
verificationRouter.get('/history', async (req, res, next) => {
  try {
    const { from, to } = req.query as Record<string, string>;
    if (!isDate(from) || !isDate(to)) throw badRequest('from·to 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.history(from, to, req.query.regionCd ? decodeURIComponent(req.query.regionCd as string) : undefined));
  } catch (e) { next(e); }
});

/** FR-019 · SC-007 · SC-008 · SC-011 */
verificationRouter.get('/summary', async (req, res, next) => {
  try {
    const { from, to } = req.query as Record<string, string>;
    if (!isDate(from) || !isDate(to)) throw badRequest('from·to 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.summary(from, to, req.query.sido as string | undefined));
  } catch (e) { next(e); }
});

/** 일상어 정확도 — 운영 화면이 통계 용어 대신 쓴다. */
verificationRouter.get('/accuracy', async (req, res, next) => {
  try {
    const { from, to } = req.query as Record<string, string>;
    if (!isDate(from) || !isDate(to)) throw badRequest('from·to 는 실재하는 YYYY-MM-DD 날짜여야 합니다');
    res.json(await svc.plainAccuracy(from, to, req.query.sido as string | undefined));
  } catch (e) { next(e); }
});
