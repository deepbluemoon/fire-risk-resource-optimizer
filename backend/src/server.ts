import express from 'express';
import { config } from './config.js';
import { errorHandler, requestLog } from './middleware/errors.js';
import { riskRouter } from './routes/risk.js';
import { causeRouter } from './routes/cause.js';
import { allocationRouter } from './routes/allocation.js';
import { verificationRouter } from './routes/verification.js';
import { opsRouter } from './routes/ops.js';

export const app = express();
app.disable('x-powered-by');
app.use(requestLog);

app.use('/api/risk', riskRouter);
app.use('/api/cause', causeRouter);
app.use('/api/allocation', allocationRouter);
app.use('/api/verification', verificationRouter);
app.use('/api/ops', opsRouter);

app.get('/api', (_req, res) => res.json({
  service: '화재위험 예측 및 자원 배분 지원 API',
  version: '0.1.0',
  domain: config.publicDomain,
  endpoints: ['/api/risk/daily', '/api/risk/high-days', '/api/risk/:regionCd/:date',
    '/api/cause/:regionCd/:date', '/api/allocation/proposal', '/api/allocation/adjustments',
    '/api/verification/history', '/api/verification/summary',
    '/api/ops/batch-status', '/api/ops/models', '/api/ops/regions', '/api/ops/grades', '/api/ops/health'],
}));

app.use((_req, res) => res.status(404).json({ error: 'NOT_FOUND', message: '없는 경로입니다' }));
app.use(errorHandler);

// 테스트에서는 START_SERVER=0 으로 두고 supertest 가 app 을 직접 잡는다
if (process.env.START_SERVER !== '0') {
  app.listen(config.port, config.host, () => {
    console.log(JSON.stringify({
      ts: new Date().toISOString(), level: 'INFO',
      msg: `backend listening on http://${config.host}:${config.port} (public: https://${config.publicDomain}/api)`,
    }));
  });
}
