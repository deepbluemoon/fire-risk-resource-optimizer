import { Router, json } from 'express';
import * as svc from '../services/allocationService.js';
import * as risk from '../services/riskService.js';
import { badRequest, HttpError, notFound } from '../middleware/errors.js';
import { todaySeoul } from '../util/date.js';

export const allocationRouter = Router();
allocationRouter.use(json({ limit: '1mb' }));

/** FR-035 · FR-037 · FR-038 */
allocationRouter.get('/proposal', async (req, res, next) => {
  try {
    const date = (req.query.date as string) || (await risk.latestDate()) || todaySeoul();
    const policy = svc.readPolicy(req.query as Record<string, unknown>);
    const out = await svc.buildProposal(date, policy, req.query.sido as string | undefined);
    if (!out) throw notFound(`${date} 의 예측이 없어 배분안을 산출할 수 없습니다`);
    res.json(out);
  } catch (e) { next(e); }
});

allocationRouter.get('/constraints', async (_req, res, next) => {
  try { res.json({ policy: svc.DEFAULT_POLICY, items: await svc.activeConstraints() }); }
  catch (e) { next(e); }
});

allocationRouter.get('/adjustments', async (req, res, next) => {
  try { res.json(await svc.listAdjustments(req.query.from as string, req.query.to as string)); }
  catch (e) { next(e); }
});

/** FR-017 · FR-039 · SC-009. 제약 위반은 422 로 거부한다 (FR-036) */
allocationRouter.post('/adjustments', async (req, res, next) => {
  try {
    const b = req.body ?? {};
    // proposalId 를 받지 않는다. 배분안은 조회할 때 DB 에 남기지 않고 저장 시점에
    // 정책값과 함께 다시 계산해 기록하므로, 화면은 '어느 날짜의 어떤 정책값을 보고
    // 고쳤는지' 만 알려주면 된다. 그래야 이력의 권장안과 조정안이 반드시 짝이 맞는다.
    for (const k of ['targetDate', 'authorId', 'authorRole', 'reason']) {
      if (b[k] == null || b[k] === '') throw badRequest(`${k} 는 필수입니다`);
    }
    if (!Array.isArray(b.items) || !b.items.length) throw badRequest('items 는 1개 이상이어야 합니다');
    const out = await svc.saveAdjustment(b);
    if (!out.ok) {
      throw new HttpError(422, 'CONSTRAINT_VIOLATION',
        '운영 제약을 위반하는 조정입니다', { violations: out.violations });
    }
    const saved = await svc.listAdjustments();
    res.status(201).json(saved.find((a) => a.adjustmentId === out.adjustmentId) ?? { adjustmentId: out.adjustmentId });
  } catch (e) { next(e); }
});
